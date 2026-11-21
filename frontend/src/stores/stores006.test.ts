// Feature 006 stores (FTASKS 12.2).
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useCalibrationStore } from '@/stores/calibrationStore';
import { useReviewStore } from '@/stores/reviewStore';
import { mockFetch } from '@/test/fetchMock';
import { item, queue, record } from '@/test/fixtures006';

afterEach(() => {
  vi.unstubAllGlobals();
  useCalibrationStore.setState({ records: [], sets: [], targets: {}, jobs: {}, preview: null, previewError: null, error: null });
  useReviewStore.setState({ queues: [], selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0, paging: false, history: {}, audits: {}, error: null });
});

describe('calibrationStore', () => {
  it('a completed job event refetches the records', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/calibration-records': { items: [record()], total: 1 } });
    useCalibrationStore.getState().applyJobEvent('job_1', 'running');
    expect(calls).toHaveLength(0);
    useCalibrationStore.getState().applyJobEvent('job_1', 'completed');
    await vi.waitFor(() => expect(useCalibrationStore.getState().records).toHaveLength(1));
    expect(useCalibrationStore.getState().jobs.job_1).toBe('completed');
  });

  it('an import refusal is kept as the preview error', async () => {
    mockFetch({ 'POST /api/v1/calibration-sets/import': () => ({ status: 409, json: { error: { code: 'ROW_KEY_CONFLICT', message: 'Two rows share row key', details: {} } } }) });
    const created = await useCalibrationStore.getState().importSet({ version_id: 'v', question: 'q', label_set: ['a', 'b'], mapping: { schema: 'dw.calibration-mapping/v1', human_label: { column: 'g', rule: 'numeric', positive_at_or_above: 1, negative_at_or_below: 0 } } });
    expect(created).toBeNull();
    expect(useCalibrationStore.getState().previewError).toContain('Two rows share row key');
  });
});

describe('reviewStore', () => {
  it('decide appends optimistically then keeps the saved decision', async () => {
    mockFetch({ 'POST /api/v1/review-items/ri_1/decisions': () => ({ status: 201, json: { id: 'rd_9', decision: 'accept', decided_by: 'Ada', decided_by_origin: 'operator' } }) });
    useReviewStore.setState({ items: [item()], queues: [queue()] });
    const done = useReviewStore.getState().decide('ri_1', { decision: 'accept' }, 'Ada');
    expect(useReviewStore.getState().items[0].pending).toBe(true);
    expect(useReviewStore.getState().items[0].latest_decision?.decided_by).toBe('Ada');
    expect(await done).toBe(true);
    expect(useReviewStore.getState().items[0].latest_decision?.id).toBe('rd_9');
    expect(useReviewStore.getState().queues[0].decided).toBe(1);
  });

  it('a refused decision is rolled back', async () => {
    mockFetch({ 'POST /api/v1/review-items/ri_1/decisions': () => ({ status: 422, json: { error: { code: 'DECISION_INVALID', message: 'Write a reason', details: {} } } }) });
    useReviewStore.setState({ items: [item()] });
    expect(await useReviewStore.getState().decide('ri_1', { decision: 'flag' }, 'Ada')).toBe(false);
    expect(useReviewStore.getState().items[0].latest_decision).toBeNull();
    expect(useReviewStore.getState().error).toBe('Write a reason');
  });

  it('opening a queue refreshes its count, so a decision made elsewhere is not read as 0', async () => {
    mockFetch({
      'GET /api/v1/review-queues': { items: [queue({ decided: 50 })], total: 1 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item()], total: 1 },
    });
    useReviewStore.setState({ queues: [queue({ decided: 0 })] });
    await useReviewStore.getState().selectQueue('rq_1');
    expect(useReviewStore.getState().queues[0].decided).toBe(50);
  });

  describe('the open queue through a refresh', () => {
    const KEY = 'midataworks-review-open-queue';
    const page2 = { items: [item({ id: 'ri_51' }), item({ id: 'ri_52' }), item({ id: 'ri_53' })], total: 53 };
    const refresh = () => useReviewStore.setState({ queues: [queue()], selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0, error: null });

    it('comes back at the same page and row', async () => {
      const { calls } = mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page2, 'GET /api/v1/review-queues': { items: [queue()], total: 1 } });
      await useReviewStore.getState().selectQueue('rq_1');
      await useReviewStore.getState().fetchItems(2);
      useReviewStore.getState().move(1);
      refresh();
      calls.length = 0;
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState()).toMatchObject({ selectedQueueId: 'rq_1', page: 2, cursor: 1 });
      expect(calls[0].path).toContain('page=2');
    });

    it('a page reached with the Next page link is kept, with no row moved after it', async () => {
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page2, 'GET /api/v1/review-queues': { items: [queue()], total: 1 } });
      await useReviewStore.getState().selectQueue('rq_1');
      await useReviewStore.getState().fetchItems(2);
      refresh();
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState()).toMatchObject({ selectedQueueId: 'rq_1', page: 2, cursor: 0 });
    });

    it('a queue already open is left alone', async () => {
      window.localStorage.setItem(KEY, JSON.stringify({ id: 'rq_1', page: 2, cursor: 1 }));
      const { calls } = mockFetch({});
      useReviewStore.setState({ queues: [queue(), queue({ id: 'rq_2' })], selectedQueueId: 'rq_2', error: null });
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState().selectedQueueId).toBe('rq_2');
      expect(calls).toHaveLength(0);
    });

    it('a row past the end now lands on the last row, and a page past the end on the first page', async () => {
      window.localStorage.setItem(KEY, JSON.stringify({ id: 'rq_1', page: 2, cursor: 40 }));
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items?page=2&limit=50': page2 });
      refresh();
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState()).toMatchObject({ page: 2, cursor: 2 });
      window.localStorage.setItem(KEY, JSON.stringify({ id: 'rq_1', page: 9, cursor: 0 }));
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items?page=9&limit=50': { items: [], total: 3 }, 'GET /api/v1/review-queues/rq_1/items?page=1&limit=50': { items: [item()], total: 3 } });
      refresh();
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState()).toMatchObject({ page: 1, cursor: 0 });
      expect(useReviewStore.getState().items).toHaveLength(1);
    });

    it('closing the view is remembered too, and a queue no longer listed is forgotten', async () => {
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page2, 'GET /api/v1/review-queues': { items: [queue()], total: 1 } });
      await useReviewStore.getState().selectQueue('rq_1');
      await useReviewStore.getState().selectQueue(null);
      refresh();
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState().selectedQueueId).toBeNull();
      window.localStorage.setItem(KEY, JSON.stringify({ id: 'rq_gone', page: 1, cursor: 0 }));
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState().selectedQueueId).toBeNull();
      expect(window.localStorage.getItem(KEY)).toBeNull();
    });

    it('closing the queue itself forgets it', async () => {
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page2, 'GET /api/v1/review-queues': { items: [queue()], total: 1 }, 'POST /api/v1/review-queues/rq_1/close': queue({ state: 'closed' }) });
      await useReviewStore.getState().selectQueue('rq_1');
      await useReviewStore.getState().closeQueue('rq_1');
      expect(window.localStorage.getItem(KEY)).toBeNull();
    });

    it('a failed list keeps the place for next time, and a value it cannot read opens nothing', async () => {
      window.localStorage.setItem(KEY, JSON.stringify({ id: 'rq_1', page: 2, cursor: 1 }));
      useReviewStore.setState({ queues: [], selectedQueueId: null, error: 'The list did not load' });
      await useReviewStore.getState().restoreOpenQueue();
      expect(window.localStorage.getItem(KEY)).not.toBeNull();
      refresh();
      window.localStorage.setItem(KEY, '{"id":"rq_1","page":"two"');
      await useReviewStore.getState().restoreOpenQueue();
      expect(useReviewStore.getState().selectedQueueId).toBeNull();
      for (const bad of [{ id: 'rq_1', page: 'two', cursor: 0 }, { id: 'rq_1', page: 0, cursor: 0 }, { id: 'rq_1', page: 1, cursor: -1 }, { id: 7, page: 1, cursor: 0 }]) {
        window.localStorage.setItem(KEY, JSON.stringify(bad));
        await useReviewStore.getState().restoreOpenQueue();
        expect(useReviewStore.getState().selectedQueueId).toBeNull();
      }
    });
  });

  it('move stops at the two ends of the queue', () => {
    const { calls } = mockFetch({});
    useReviewStore.setState({ items: [item(), item({ id: 'ri_2' })], total: 2, cursor: 0 });
    useReviewStore.getState().move(-1);
    expect(useReviewStore.getState().cursor).toBe(0);
    useReviewStore.getState().move(5);
    expect(useReviewStore.getState().cursor).toBe(1);
    expect(calls).toHaveLength(0);
  });

  it('move goes on past the end of a page, once, and back to the last row of the one before', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/review-queues/rq_1/items': { items: [item({ id: 'ri_a' }), item({ id: 'ri_b' })], total: 52 },
    });
    useReviewStore.setState({ selectedQueueId: 'rq_1', items: [item()], total: 52, page: 1, cursor: 0 });
    useReviewStore.getState().move(1);
    useReviewStore.getState().move(1); // a second press while the page loads does not skip a page
    await vi.waitFor(() => expect(useReviewStore.getState().page).toBe(2));
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toContain('page=2');
    expect(useReviewStore.getState().cursor).toBe(0);
    useReviewStore.getState().move(-1);
    await vi.waitFor(() => expect(useReviewStore.getState().page).toBe(1));
    expect(calls[1].path).toContain('page=1');
    expect(useReviewStore.getState().cursor).toBe(1);
  });

  describe('going to a row by its number', () => {
    const page4 = { items: Array.from({ length: 50 }, (_, i) => item({ id: `ri_${151 + i}` })), total: 300 };
    const onPage1 = () => useReviewStore.setState({ selectedQueueId: 'rq_1', items: page4.items, total: 300, page: 1, cursor: 3 });

    it('on another page loads that page once and lands on that row, and a refresh comes back to it', async () => {
      const { calls } = mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page4 });
      onPage1();
      expect(useReviewStore.getState().goToRow(197)).toBe(true);
      await vi.waitFor(() => expect(useReviewStore.getState().page).toBe(4));
      expect(useReviewStore.getState().cursor).toBe(46);
      expect(calls).toHaveLength(1);
      expect(calls[0].path).toContain('page=4');
      expect(JSON.parse(window.localStorage.getItem('midataworks-review-open-queue')!)).toEqual({ id: 'rq_1', page: 4, cursor: 46 });
    });

    it('on this page moves without loading anything, and a refresh comes back to it', () => {
      const { calls } = mockFetch({});
      onPage1();
      expect(useReviewStore.getState().goToRow(50)).toBe(true);
      expect(useReviewStore.getState()).toMatchObject({ page: 1, cursor: 49 });
      expect(calls).toHaveLength(0);
      expect(JSON.parse(window.localStorage.getItem('midataworks-review-open-queue')!)).toEqual({ id: 'rq_1', page: 1, cursor: 49 });
    });

    it('takes the first row of a page and the last row of the queue', async () => {
      mockFetch({ 'GET /api/v1/review-queues/rq_1/items': page4 });
      onPage1();
      useReviewStore.getState().goToRow(51);
      await vi.waitFor(() => expect(useReviewStore.getState()).toMatchObject({ page: 2, cursor: 0, paging: false }));
      useReviewStore.getState().goToRow(300);
      await vi.waitFor(() => expect(useReviewStore.getState()).toMatchObject({ page: 6, cursor: 49, paging: false }));
    });

    it.each([0, -1, 301, 1.5, Number.NaN, Number.POSITIVE_INFINITY])('refuses %s and stays where it is', (row) => {
      const { calls } = mockFetch({});
      onPage1();
      expect(useReviewStore.getState().goToRow(row)).toBe(false);
      expect(useReviewStore.getState()).toMatchObject({ page: 1, cursor: 3 });
      expect(calls).toHaveLength(0);
    });

    it('refuses while a page loads, or a late answer would land on the wrong row', () => {
      const { calls } = mockFetch({});
      onPage1();
      useReviewStore.setState({ paging: true });
      expect(useReviewStore.getState().goToRow(197)).toBe(false);
      expect(calls).toHaveLength(0);
    });
  });

  it('a page that fails to load keeps the row and says why', async () => {
    mockFetch({ 'GET /api/v1/review-queues/rq_1/items': () => ({ status: 500, json: { error: { code: 'BOOM', message: 'The page did not load', details: {} } } }) });
    useReviewStore.setState({ selectedQueueId: 'rq_1', items: [item()], total: 52, page: 1, cursor: 0 });
    useReviewStore.getState().move(1);
    await vi.waitFor(() => expect(useReviewStore.getState().error).toBe('The page did not load'));
    expect(useReviewStore.getState()).toMatchObject({ page: 1, cursor: 0, paging: false });
  });
});

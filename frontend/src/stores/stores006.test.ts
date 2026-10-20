// Feature 006 stores (FTASKS 12.2).
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useCalibrationStore } from '@/stores/calibrationStore';
import { useReviewStore } from '@/stores/reviewStore';
import { mockFetch } from '@/test/fetchMock';
import { item, queue, record } from '@/test/fixtures006';

afterEach(() => {
  vi.unstubAllGlobals();
  useCalibrationStore.setState({ records: [], sets: [], targets: {}, jobs: {}, preview: null, previewError: null, error: null });
  useReviewStore.setState({ queues: [], selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0, history: {}, audits: {}, error: null });
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

  it('move stays inside the page', () => {
    useReviewStore.setState({ items: [item(), item({ id: 'ri_2' })], cursor: 0 });
    useReviewStore.getState().move(-1);
    expect(useReviewStore.getState().cursor).toBe(0);
    useReviewStore.getState().move(5);
    expect(useReviewStore.getState().cursor).toBe(1);
  });
});

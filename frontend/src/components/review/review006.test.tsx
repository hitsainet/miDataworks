// Feature 006 review screen (FTASKS 12.5 – 12.9).
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import ts from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReviewPanel } from '@/components/panels/ReviewPanel';
import { getPanel } from '@/config/panels';
import { useReviewStore } from '@/stores/reviewStore';
import { mockFetch } from '@/test/fetchMock';
import { item, queue } from '@/test/fixtures006';

import { AuditBanner } from './AuditBanner';
import { DecisionBar } from './DecisionBar';
import { DrawAuditDialog } from './DrawAuditDialog';
import { ItemText, ItemView } from './ItemView';

const reset = () => useReviewStore.setState({ queues: [], selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0, paging: false, history: {}, audits: {}, error: null, showClosed: false });

afterEach(() => {
  vi.unstubAllGlobals();
  reset();
});

describe('DecisionBar', () => {
  it('names its actions; reject only on external queues; reason required', () => {
    const onDecide = vi.fn();
    const { rerender } = render(<DecisionBar kind="label_review" labels={['a', 'b']} hidden={false} onDecide={onDecide} />);
    expect(screen.getByRole('button', { name: 'Accept label' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Override label' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Flag row' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Reject candidate' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'wrong' } });
    // a reason alone is not a choice: no label is chosen in advance
    expect(screen.getByRole('button', { name: 'Override label' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'a' }));
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Override label' }));
    expect(onDecide).toHaveBeenCalledWith({ decision: 'override', override_label: 'a', reason: 'wrong' });
    rerender(<DecisionBar kind="external" labels={['a', 'b']} hidden={false} onDecide={onDecide} />);
    expect(screen.getByRole('button', { name: 'Reject candidate' })).toBeInTheDocument();
  });

  it('calibration labeling with hidden output offers one button per label, and no accept', () => {
    const onDecide = vi.fn();
    render(<DecisionBar kind="calibration_labeling" labels={['no', 'yes', 'unsure']} hidden onDecide={onDecide} />);
    expect(screen.queryByRole('button', { name: 'Accept label' })).toBeNull();
    // no chosen label to carry over: nothing to select, nothing to press o on
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Assign label' })).toBeNull();
    expect(document.querySelector('[data-key="o"]')).toBeNull();
    // the key on each button is its place in the label set, and the button shows it
    expect(screen.getByRole('button', { name: 'no' })).toHaveAttribute('data-key', '1');
    expect(screen.getByRole('button', { name: 'no' })).toHaveTextContent('1no');
    expect(screen.getByRole('button', { name: 'yes' })).toHaveAttribute('data-key', '2');
    expect(screen.getByRole('button', { name: 'yes' })).toHaveTextContent('2yes');
    expect(screen.getByRole('button', { name: 'unsure' })).toHaveAttribute('data-key', '3');
    expect(screen.getByRole('button', { name: 'unsure' })).toHaveAttribute('aria-keyshortcuts', '3');
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'yes' }));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith({ decision: 'override', override_label: 'yes', reason: undefined });
  });

  it('calibration labeling keeps the flag, the reason and, when output is shown, the accept', () => {
    const onDecide = vi.fn();
    render(<DecisionBar kind="calibration_labeling" labels={['a', 'b']} hidden={false} onDecide={onDecide} />);
    expect(screen.getByRole('button', { name: 'Accept label' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Flag row' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'torn' } });
    fireEvent.click(screen.getByRole('button', { name: 'a' }));
    expect(onDecide).toHaveBeenCalledWith({ decision: 'override', override_label: 'a', reason: 'torn' });
    fireEvent.click(screen.getByRole('button', { name: 'Flag row' }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: 'flag', reason: 'torn' });
  });

  it('a tenth label is a button with no key', () => {
    const labels = Array.from({ length: 10 }, (_, i) => `l${i}`);
    render(<DecisionBar kind="calibration_labeling" labels={labels} hidden onDecide={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'l8' })).toHaveAttribute('data-key', '9');
    expect(screen.getByRole('button', { name: 'l9' })).not.toHaveAttribute('data-key');
  });

  it('other queue kinds choose by button with no dropdown, one at a time, and keep the override', () => {
    const onDecide = vi.fn();
    render(<DecisionBar kind="audit" labels={['a', 'b', 'c']} hidden={false} onDecide={onDecide} />);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(document.querySelector('[data-key="o"]')).not.toBeNull();
    expect(document.querySelector('[data-key="1"]')).toBeNull();
    const pressed = () => screen.getByTestId('label-choices').querySelectorAll('[aria-pressed="true"]');
    expect(pressed()).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'a' }));
    fireEvent.click(screen.getByRole('button', { name: 'c' }));
    expect(pressed()).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'c', pressed: true })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Override label' }));
    expect(onDecide).toHaveBeenCalledWith({ decision: 'override', override_label: 'c', reason: 'wrong' });
  });

  it('the chosen button follows the answer while the row stays on screen, as on the last row of a queue', () => {
    const props = { kind: 'calibration_labeling' as const, labels: ['yes', 'no'], hidden: true, onDecide: vi.fn() };
    const { rerender } = render(<DecisionBar {...props} chosen="yes" />);
    rerender(<DecisionBar {...props} chosen="no" />);
    expect(screen.getByRole('button', { name: 'no', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'yes', pressed: false })).toBeInTheDocument();
  });

  it.each([[['yes', 'no']], [['a', 'b', 'c', 'd', 'e']]])('every label of %j is a side-by-side button and only the current answer reads as chosen', (labels) => {
    render(<DecisionBar kind="calibration_labeling" labels={labels} hidden chosen={labels[1]} onDecide={vi.fn()} />);
    const choices = screen.getByTestId('label-choices');
    expect(choices.className).toContain('flex');
    expect(Array.from(choices.querySelectorAll('button')).map((b) => b.getAttribute('aria-pressed'))).toEqual(labels.map((_, i) => String(i === 1)));
  });
});

describe('ItemView', () => {
  it('hides model output with the reason on a calibration queue', () => {
    render(<ItemView item={item({ model_output_hidden: true, model_snapshot: null })} queue={queue({ kind: 'calibration_labeling', show_model_output: false })} />);
    expect(screen.getByTestId('model-hidden')).toHaveTextContent('Model output hidden');
    expect(screen.queryByTestId('model-snapshot')).toBeNull();
  });

  it('renders external text as text, with the miForge chip', () => {
    const ext = item({ row_key: null, external_id: 'c1', text: null, payload: { prompt: 'p', completion: '<b>bold</b><script>x()</script>', provenance: { run_id: 'mf1', step: 3 } } });
    const { container } = render(<><ItemText item={ext} /><ItemView item={ext} queue={queue({ kind: 'external', origin_app: 'miforge' })} /></>);
    expect(screen.getByTestId('item-completion')).toHaveTextContent('<b>bold</b><script>x()</script>');
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByTestId('miforge-chip')).toHaveTextContent('miForge · run mf1 · step 3');
  });
});

describe('no HTML rendering in review components (FTASKS 12.8)', () => {
  it('no review or calibration component uses dangerouslySetInnerHTML', () => {
    const dirs = [join(__dirname), join(__dirname, '..', 'calibration'), join(__dirname, '..', 'panels')];
    const files = dirs.flatMap((d) => readdirSync(d).filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx')).map((f) => join(d, f)));
    expect(files.length).toBeGreaterThan(10);
    for (const file of files) {
      const sf = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const found: string[] = [];
      const visit = (node: ts.Node) => {
        if (ts.isJsxAttribute(node) && node.name.getText(sf) === 'dangerouslySetInnerHTML') found.push(file);
        ts.forEachChild(node, visit);
      };
      visit(sf);
      expect(found, file).toEqual([]);
    }
  });
});

describe('AuditBanner and DrawAuditDialog', () => {
  it('shows progress and the completed result', () => {
    const { rerender } = render(<AuditBanner status={{ version_id: 'v', state: 'in_progress', audit_id: 'au', queue_id: 'rq', size: 100, decided: 40, strata: null, result: null }} />);
    expect(screen.getByTestId('audit-banner')).toHaveTextContent('40 of 100 rows decided');
    rerender(<AuditBanner status={{ version_id: 'v', state: 'complete', audit_id: 'au', queue_id: 'rq', size: 50, decided: 50, strata: null, result: { accept: 48, override: 1, flag: 1, decided: 50, size: 50, agreement_share: 0.96 } }} />);
    expect(screen.getByTestId('audit-banner')).toHaveTextContent('48 accepted, 1 overridden, 1 flagged of 50 rows (agreement 96.0%)');
  });

  it('size 50 to 100, default 100', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/versions/v1/audit': () => ({ status: 201, json: { version_id: 'v1', state: 'in_progress' } }), 'GET /api/v1/review-queues': { items: [], total: 0 } });
    const onClose = vi.fn();
    render(<DrawAuditDialog onClose={onClose} />);
    expect(screen.getByLabelText('Audit size')).toHaveValue('100');
    fireEvent.change(screen.getByLabelText('Version ID'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Audit size'), { target: { value: '49' } });
    expect(screen.getByRole('button', { name: 'Draw audit sample' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Audit size'), { target: { value: '101' } });
    expect(screen.getByTestId('audit-size-hint')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Audit size'), { target: { value: '50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Draw audit sample' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ size: 50 });
  });
});

describe('ReviewPanel on a calibration-labeling queue', () => {
  const decisionRoute = vi.fn((body: unknown): { status: number; json: unknown } => ({ status: 201, json: { id: 'rd_1', item_id: 'ri_1', queue_id: 'rq_1', row_key: 'e', decision: 'override', ...(body as object), reason: null, decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: false, version_id: 'v1', created_at: '2026-10-07T10:00:00Z' } }));
  beforeEach(() => {
    decisionRoute.mockClear();
    mockFetch({
      'GET /api/v1/review-queues': { items: [queue({ kind: 'calibration_labeling', show_model_output: false, label_set: ['no', 'yes'] })], total: 1 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item({ model_output_hidden: true, model_snapshot: null }), item({ id: 'ri_2', position: 1, model_output_hidden: true, model_snapshot: null, text: { text: 'Second row' } })], total: 2 },
      'GET /api/v1/review-items/ri_1/decisions': [],
      'GET /api/v1/review-items/ri_2/decisions': [],
      'POST /api/v1/review-items/ri_1/decisions': decisionRoute,
    });
  });

  async function open() {
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getByTestId('queue-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('queue-card'));
    await waitFor(() => expect(screen.getByTestId('item-view')).toBeInTheDocument());
    return screen.getByTestId('review-panel');
  }

  it('a digit assigns that label in label-set order, once, and moves on', async () => {
    const panel = await open();
    fireEvent.keyDown(panel, { key: '2' });
    await waitFor(() => expect(decisionRoute).toHaveBeenCalledTimes(1));
    expect(decisionRoute.mock.calls[0][0]).toEqual({ decision: 'override', override_label: 'yes' });
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Second row'));
  });

  it('o records nothing: no default label can be assigned by accident', async () => {
    const panel = await open();
    fireEvent.keyDown(panel, { key: 'o' });
    fireEvent.keyDown(panel, { key: '3' }); // past the label set
    expect(decisionRoute).not.toHaveBeenCalled();
  });

  it('a digit typed in the reason field is the reason, not a label', async () => {
    await open();
    const reason = screen.getByLabelText('Reason');
    fireEvent.keyDown(reason, { key: '1' });
    expect(decisionRoute).not.toHaveBeenCalled();
  });

  it('a held digit does not label the rows it passes', async () => {
    const panel = await open();
    fireEvent.keyDown(panel, { key: '1', repeat: true });
    expect(decisionRoute).not.toHaveBeenCalled();
  });

  it('a row shows its current answer on return, a new answer replaces it, and the same answer adds nothing', async () => {
    const panel = await open();
    const answered = () => screen.getByTestId('label-choices').querySelectorAll('[aria-pressed="true"]');
    expect(answered()).toHaveLength(0);
    fireEvent.keyDown(panel, { key: '1' });
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Second row'));
    expect(answered()).toHaveLength(0);
    fireEvent.keyDown(panel, { key: 'k' });
    expect(screen.getByRole('button', { name: 'no', pressed: true })).toBeInTheDocument();
    expect(answered()).toHaveLength(1);
    expect(decisionRoute).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(panel, { key: '1' }); // the same answer: moves on and records nothing
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Second row'));
    expect(decisionRoute).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(panel, { key: 'k' });
    fireEvent.keyDown(panel, { key: '2' }); // a different answer replaces it
    await waitFor(() => expect(decisionRoute).toHaveBeenCalledTimes(2));
    expect(decisionRoute.mock.calls[1][0]).toMatchObject({ override_label: 'yes' });
    fireEvent.keyDown(panel, { key: 'k' });
    expect(screen.getByRole('button', { name: 'yes', pressed: true })).toBeInTheDocument();
    expect(answered()).toHaveLength(1);
  });

  it('the panel opens the queue this browser had open, at the same row, when the page loads', async () => {
    window.localStorage.setItem('midataworks-review-open-queue', JSON.stringify({ id: 'rq_1', page: 1, cursor: 1 }));
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getByTestId('item-position')).toHaveTextContent('Row 2 of'));
    expect(screen.getByTestId('item-text')).toHaveTextContent('Second row');
  });

  it('the text comes right after the question, and everything else follows in its order', async () => {
    await open();
    const order = ['queue-view-head', 'item-under-review', 'close-queue', 'item-position', 'label-choices', 'item-view'].map((id) => screen.getByTestId(id));
    for (let i = 1; i < order.length; i += 1) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByTestId('queue-view-head').nextElementSibling).toBe(screen.getByTestId('item-under-review'));
    expect(screen.getByTestId('item-under-review')).toContainElement(screen.getByTestId('item-text'));
  });

  it('deciding the last row of a page goes on to the next page, and a fast second key does not re-decide it', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const decided = vi.fn((body: unknown) => ({ status: 201, json: { id: 'rd_1', item_id: 'ri_1', queue_id: 'rq_1', row_key: 'e', decision: 'override', override_label: (body as { override_label: string }).override_label, reason: 'label assigned by the reviewer', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: false, version_id: 'v1', created_at: '2026-11-01T10:00:00Z' } }));
    const hidden = { model_output_hidden: true };
    const { fn } = mockFetch({
      'GET /api/v1/review-queues': { items: [queue({ kind: 'calibration_labeling', label_set: ['yes', 'no'] })], total: 1 },
      'GET /api/v1/review-queues/rq_1/items?page=1&limit=50': { items: [item(hidden)], total: 51 },
      'GET /api/v1/review-queues/rq_1/items?page=2&limit=50': { items: [item({ ...hidden, id: 'ri_51', text: { text: 'Row of the next page' } })], total: 51 },
      'GET /api/v1/review-items/ri_1/decisions': [],
      'GET /api/v1/review-items/ri_51/decisions': [],
      'POST /api/v1/review-items/ri_1/decisions': decided,
    });
    const answer = fn.getMockImplementation()!;
    fn.mockImplementation(async (input, init) => {
      if (String(input).includes('page=2')) await gate;
      return answer(input, init);
    });
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getByTestId('queue-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('queue-card'));
    await waitFor(() => expect(screen.getByTestId('item-view')).toBeInTheDocument());
    const panel = screen.getByTestId('review-panel');
    fireEvent.keyDown(panel, { key: '2' });
    await waitFor(() => expect(useReviewStore.getState().paging).toBe(true));
    fireEvent.keyDown(panel, { key: '1' });
    release();
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Row of the next page'));
    expect(screen.getByTestId('item-position')).toHaveTextContent('Row 51 of 51');
    expect(decided).toHaveBeenCalledTimes(1);
    expect(decided.mock.calls[0][0]).toMatchObject({ override_label: 'no' });
  });

  describe('Go to row', () => {
    const hidden = { model_output_hidden: true };
    const typeRow = (value: string) => {
      screen.getByLabelText('Go to row').focus();
      fireEvent.change(screen.getByLabelText('Go to row'), { target: { value } });
      fireEvent.submit(screen.getByTestId('go-to-row'));
    };
    const decided = vi.fn((body: unknown) => ({ status: 201, json: { id: 'rd_1', item_id: 'ri_197', queue_id: 'rq_1', row_key: 'e', decision: 'override', override_label: (body as { override_label: string }).override_label, reason: 'label assigned by the reviewer', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: false, version_id: 'v1', created_at: '2026-11-02T10:00:00Z' } }));

    async function openLong() {
      decided.mockClear();
      mockFetch({
        'GET /api/v1/review-queues': { items: [queue({ kind: 'calibration_labeling', label_set: ['yes', 'no'] })], total: 1 },
        'GET /api/v1/review-queues/rq_1/items?page=1&limit=50': { items: Array.from({ length: 50 }, (_, i) => item({ ...hidden, id: `ri_${i + 1}`, text: { text: `Text of row ${i + 1}.` } })), total: 300 },
        'GET /api/v1/review-queues/rq_1/items?page=4&limit=50': { items: Array.from({ length: 50 }, (_, i) => item({ ...hidden, id: `ri_${i + 151}`, text: { text: `Text of row ${i + 151}.` } })), total: 300 },
        'GET /api/v1/review-items/ri_1/decisions': [],
        'GET /api/v1/review-items/ri_197/decisions': [],
        'GET /api/v1/review-items/ri_198/decisions': [],
        'POST /api/v1/review-items/ri_197/decisions': decided,
      });
      await open();
      return screen.getByTestId('review-panel');
    }

    it('shows the row with that number, on another page, ready for its answer key', async () => {
      const panel = await openLong();
      typeRow('197');
      await waitFor(() => expect(screen.getByTestId('item-position')).toHaveTextContent('Row 197 of 300'));
      expect(screen.getByTestId('item-text')).toHaveTextContent('Text of row 197.');
      expect(screen.getByLabelText('Go to row')).toHaveValue('');
      expect(document.activeElement).toBe(panel);
      fireEvent.keyDown(panel, { key: '1' });
      await waitFor(() => expect(decided).toHaveBeenCalledTimes(1));
      expect(decided.mock.calls[0][0]).toMatchObject({ override_label: 'yes' });
    });

    it('on the same page also hands the keys back', async () => {
      const panel = await openLong();
      typeRow('7');
      expect(screen.getByTestId('item-position')).toHaveTextContent('Row 7 of 300');
      expect(document.activeElement).toBe(panel);
    });

    it('a refusal goes once the number is good, even with nothing retyped', async () => {
      await openLong();
      typeRow('301');
      expect(await screen.findByText('Enter a row from 1 to 300.')).toBeInTheDocument();
      useReviewStore.setState({ total: 301 });
      fireEvent.submit(screen.getByTestId('go-to-row'));
      await waitFor(() => expect(screen.queryByText(/Enter a row from/)).not.toBeInTheDocument());
    });

    it('another queue starts with an empty field and no refusal', async () => {
      await openLong();
      typeRow('301');
      expect(await screen.findByText('Enter a row from 1 to 300.')).toBeInTheDocument();
      expect(screen.getByLabelText('Go to row')).toHaveValue('301');
      mockFetch({
        'GET /api/v1/review-queues': { items: [queue({ kind: 'calibration_labeling', label_set: ['yes', 'no'] }), queue({ id: 'rq_2', kind: 'calibration_labeling', label_set: ['yes', 'no'] })], total: 2 },
        'GET /api/v1/review-queues/rq_2/items': { items: [item({ ...hidden, id: 'ri_q2', queue_id: 'rq_2', text: { text: 'Row of the other queue.' } })], total: 1 },
        'GET /api/v1/review-items/ri_q2/decisions': [],
      });
      await useReviewStore.getState().selectQueue('rq_2');
      await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Row of the other queue.'));
      expect(screen.getByLabelText('Go to row')).toHaveValue('');
      expect(screen.queryByText(/Enter a row from/)).not.toBeInTheDocument();
    });

    it.each(['301', '0', '12a', '1.5', '-3', '', '1e2', '0x10'])('refuses %j, says which rows there are, and keeps the row on screen', async (value) => {
      await openLong();
      typeRow(value);
      expect(await screen.findByRole('alert')).toHaveTextContent('Enter a row from 1 to 300.');
      expect(screen.getByTestId('item-position')).toHaveTextContent('Row 1 of 300');
      fireEvent.change(screen.getByLabelText('Go to row'), { target: { value: '2' } });
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('a digit typed in the field is the row number, not an answer', async () => {
      await openLong();
      fireEvent.keyDown(screen.getByLabelText('Go to row'), { key: '1' });
      expect(decided).not.toHaveBeenCalled();
    });

    it('takes no number while a page loads', async () => {
      await openLong();
      useReviewStore.setState({ paging: true });
      typeRow('197');
      expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled();
      expect(screen.getByTestId('item-position')).toHaveTextContent('Row 1 of 300');
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  it('after a click the next row still takes keys: focus comes back to the panel', async () => {
    const panel = await open();
    const button = screen.getByRole('button', { name: 'no' });
    button.focus();
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Second row'));
    await waitFor(() => expect(document.activeElement).toBe(panel));
  });
});

describe('ReviewPanel keyboard and decisions', () => {
  let calls: Array<{ method: string; path: string; body: unknown }>;
  const decisionRoute = vi.fn((body: unknown): { status: number; json: unknown } => ({ status: 201, json: { id: 'rd_1', item_id: 'ri_1', queue_id: 'rq_1', row_key: 'e', ...(body as object), override_label: null, reason: 'accepted the model label', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: true, version_id: 'v1', created_at: '2026-10-07T10:00:00Z' } }));
  beforeEach(() => {
    decisionRoute.mockClear();
    ({ calls } = mockFetch({
      'GET /api/v1/review-queues': { items: [queue({ origin_app: 'miforge', kind: 'external' })], total: 1 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item(), item({ id: 'ri_2', position: 1, text: { text: 'Second row' } })], total: 2 },
      'GET /api/v1/review-items/ri_1/decisions': [],
      'GET /api/v1/review-items/ri_2/decisions': [],
      'POST /api/v1/review-items/ri_1/decisions': decisionRoute,
    }));
  });

  async function open() {
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getByTestId('queue-card')).toBeInTheDocument());
    expect(screen.getByTestId('miforge-chip')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('queue-card'));
    await waitFor(() => expect(screen.getByTestId('item-view')).toBeInTheDocument());
  }

  it('j and k move between rows', async () => {
    await open();
    const panel = screen.getByTestId('review-panel');
    fireEvent.keyDown(panel, { key: 'j' });
    expect(screen.getByTestId('item-text')).toHaveTextContent('Second row');
    fireEvent.keyDown(panel, { key: 'k' });
    expect(screen.getByTestId('item-text')).toHaveTextContent('cat mayor');
  });

  it('a accepts; the decision is recorded and the cursor moves on', async () => {
    await open();
    fireEvent.keyDown(screen.getByTestId('review-panel'), { key: 'a' });
    await waitFor(() => expect(decisionRoute).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'accept' });
    await waitFor(() => expect(screen.getByTestId('item-text')).toHaveTextContent('Second row'));
  });

  it('o, f and r do nothing without a reason, and act once one is written', async () => {
    await open();
    const panel = screen.getByTestId('review-panel');
    for (const key of ['o', 'f', 'r']) fireEvent.keyDown(panel, { key });
    expect(decisionRoute).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'off topic' } });
    fireEvent.keyDown(panel, { key: 'r' });
    await waitFor(() => expect(decisionRoute).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'reject', reason: 'off topic' });
  });

  it('o and f send their decisions', async () => {
    await open();
    const panel = screen.getByTestId('review-panel');
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'flat' } });
    fireEvent.keyDown(panel, { key: 'f' });
    await waitFor(() => expect(decisionRoute).toHaveBeenCalledTimes(1));
    expect(decisionRoute.mock.calls[0][0]).toEqual({ decision: 'flag', reason: 'flat' });
  });

  it('a digit does nothing on a queue with no label keys', async () => {
    await open();
    fireEvent.keyDown(screen.getByTestId('review-panel'), { key: '1' });
    expect(decisionRoute).not.toHaveBeenCalled();
  });

  it('typing in a field never triggers a shortcut', async () => {
    await open();
    fireEvent.keyDown(screen.getByLabelText('Reason'), { key: 'a' });
    expect(decisionRoute).not.toHaveBeenCalled();
  });

  it('a refused decision rolls back and shows the reason', async () => {
    decisionRoute.mockImplementationOnce(() => ({ status: 403, json: { error: { code: 'AGENT_DECISION_NOT_ALLOWED', message: 'Agents can accept or flag a row', details: {} } } }));
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Accept label' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Agents can accept or flag a row'));
    expect(useReviewStore.getState().items[0].latest_decision).toBeNull();
  });
});

// Suite tracker 4.1 / D2: closing a queue, and the list leaving closed queues out by default.
describe('ReviewPanel closed queues', () => {
  const closedAt = '2026-10-16T12:00:00Z';
  it('asks the server for open queues only, and for every queue once "Show closed" is ticked', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/review-queues': { items: [queue()], total: 1 } });
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getByTestId('queue-card')).toBeInTheDocument());
    const lists = () => calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/v1/review-queues?'));
    expect(lists().map((c) => c.path)).toEqual(['/api/v1/review-queues?limit=200&state=open']);
    fireEvent.click(screen.getByTestId('show-closed'));
    await waitFor(() => expect(lists()).toHaveLength(2));
    expect(lists()[1].path).toBe('/api/v1/review-queues?limit=200');
  });

  it('"Close this queue" posts one close for that queue and drops it from the open list', async () => {
    const closeRoute = vi.fn(() => ({ json: queue({ state: 'closed', closed_at: closedAt, closed_by: 'Ada', closed_by_origin: 'operator' }) }));
    const { calls } = mockFetch({
      'GET /api/v1/review-queues': { items: [queue()], total: 1 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item()], total: 1 },
      'GET /api/v1/review-items/ri_1/decisions': [],
      'POST /api/v1/review-queues/rq_1/close': closeRoute,
    });
    vi.stubGlobal('confirm', () => true);
    render(<ReviewPanel panel={getPanel('review')} />);
    fireEvent.click(await screen.findByTestId('queue-card'));
    fireEvent.click(await screen.findByTestId('close-queue'));
    await waitFor(() => expect(closeRoute).toHaveBeenCalledTimes(1));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: '/api/v1/review-queues/rq_1/close', body: {} }]);
    await waitFor(() => expect(screen.queryByTestId('queue-card')).toBeNull());
  });

  it('a closed queue shows who closed it and offers no decision bar', async () => {
    mockFetch({
      'GET /api/v1/review-queues': { items: [queue({ state: 'closed', closed_at: closedAt, closed_by: 'agent:miforge', closed_by_origin: 'agent', close_reason: 'measurement done' })], total: 1 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item()], total: 1 },
      'GET /api/v1/review-items/ri_1/decisions': [],
    });
    useReviewStore.setState({ showClosed: true });
    render(<ReviewPanel panel={getPanel('review')} />);
    fireEvent.click(await screen.findByTestId('queue-card'));
    expect(await screen.findByTestId('queue-closed-note')).toHaveTextContent('This queue is closed by agent:miforge (measurement done)');
    await waitFor(() => expect(screen.getByTestId('item-view')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Accept label' })).toBeNull();
    expect(screen.queryByTestId('close-queue')).toBeNull();
  });
});

// Operator, 2026-10-31: the same queue card closes the open queue; another switches; the X and
// Escape close it.
describe('ReviewPanel: the open queue toggles from its card', () => {
  it('opens, closes on a second click, switches, and closes by X and Escape', async () => {
    mockFetch({
      'GET /api/v1/review-queues': { items: [queue(), queue({ id: 'rq_2', question: 'Second queue?' })], total: 2 },
      'GET /api/v1/review-queues/rq_1/items': { items: [item()], total: 1 },
      'GET /api/v1/review-queues/rq_2/items': { items: [item()], total: 1 },
      'GET /api/v1/review-items/ri_1/decisions': [],
    });
    render(<ReviewPanel panel={getPanel('review')} />);
    await waitFor(() => expect(screen.getAllByTestId('queue-card')).toHaveLength(2));
    const [first, second] = screen.getAllByTestId('queue-card');
    const head = () => screen.queryByTestId('queue-view-head');
    fireEvent.click(first);
    await waitFor(() => expect(head()).toBeInTheDocument());
    expect(useReviewStore.getState().selectedQueueId).toBe('rq_1');
    fireEvent.click(first);
    await waitFor(() => expect(head()).toBeNull());
    fireEvent.click(first);
    fireEvent.click(second);
    await waitFor(() => expect(head()).toHaveTextContent('Second queue?'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(head()).toBeNull());
    fireEvent.click(second);
    await waitFor(() => expect(head()).toBeInTheDocument());
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(head()).toBeNull());
  });
});

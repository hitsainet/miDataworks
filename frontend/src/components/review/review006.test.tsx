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
import { ItemView } from './ItemView';

const reset = () => useReviewStore.setState({ queues: [], selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0, history: {}, audits: {}, error: null, showClosed: false });

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
    fireEvent.click(screen.getByRole('button', { name: 'Override label' }));
    expect(onDecide).toHaveBeenCalledWith({ decision: 'override', override_label: 'a', reason: 'wrong' });
    rerender(<DecisionBar kind="external" labels={['a', 'b']} hidden={false} onDecide={onDecide} />);
    expect(screen.getByRole('button', { name: 'Reject candidate' })).toBeInTheDocument();
  });

  it('calibration labeling with hidden output assigns, and offers no accept', () => {
    render(<DecisionBar kind="calibration_labeling" labels={['a', 'b']} hidden onDecide={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Accept label' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Assign label' })).toBeEnabled();
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
    const { container } = render(<ItemView item={ext} queue={queue({ kind: 'external', origin_app: 'miforge' })} />);
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

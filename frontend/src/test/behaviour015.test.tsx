// Feature 015 (behaviour dataset support) on the real panels, stores and API clients against a fetch
// stub: each test asserts the payload AND the call count, so removing a field from a request body,
// a store call or a component mount turns it red.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RubricLibrary } from '@/components/labeling/RubricLibrary';
import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { ChecksPanel } from '@/components/publishing/ChecksPanel';
import { SendProgress } from '@/components/detectorSets/SendProgress';
import { SourcesList } from '@/components/sources/SourcesList';
import { getPanel } from '@/config/panels';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { mockFetch } from '@/test/fetchMock';
import { SOURCE } from '@/test/fixtures001';
import { SEND, SET } from '@/test/fixtures009';
import type { CheckRun } from '@/types/publishing';
import type { Rubric } from '@/types/labeling';

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  useDetectorSetsStore.setState({ sets: [], current: null, checks: null, send: null, results: null, pendingApproval: null, error: null, notice: null });
  useLabelingStore.setState({ rubrics: [], rubricError: null, error: null });
});

const byCall = (calls: Array<{ method: string; path: string; body: unknown }>, method: string, path: string) =>
  calls.filter((c) => c.method === method && c.path === path);

describe('detector set behaviour key', () => {
  it('the new-set form sends behaviour_key once with the set', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/detector-sets': { items: [], total: 0 },
      'POST /api/v1/detector-sets': () => ({ status: 201, json: { ...SET, behaviour_key: 'humor' } }),
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    fireEvent.click(await screen.findByRole('button', { name: 'New detector set' }));
    fireEvent.change(screen.getByLabelText('Detector set name'), { target: { value: 'humor-set' } });
    fireEvent.change(screen.getByLabelText('Behaviour key'), { target: { value: 'Humor' } });
    expect(screen.getByRole('button', { name: 'Create detector set' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Behaviour key'), { target: { value: 'humor' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create detector set' }));
    await waitFor(() => expect(byCall(calls, 'POST', '/api/v1/detector-sets')).toHaveLength(1));
    expect(byCall(calls, 'POST', '/api/v1/detector-sets')[0].body).toMatchObject({ name: 'humor-set', behaviour_key: 'humor' });
  });

  it('the set detail saves a changed key through PATCH', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/detector-sets': { items: [SET], total: 1 },
      'GET /api/v1/detector-sets/dts_1': SET,
      'GET /api/v1/detector-sets/dts_1/results': () => ({ status: 404, json: { error: { code: 'x', message: 'none', details: {} } } }),
      'PATCH /api/v1/detector-sets/dts_1': { ...SET, behaviour_key: 'pii' },
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    fireEvent.click(await screen.findByTestId('set-card-humor-set'));
    await screen.findByTestId('behaviour-key-editor');
    fireEvent.change(screen.getByLabelText('Behaviour key'), { target: { value: 'pii' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save behaviour key' }));
    await waitFor(() => expect(byCall(calls, 'PATCH', '/api/v1/detector-sets/dts_1')).toHaveLength(1));
    expect(byCall(calls, 'PATCH', '/api/v1/detector-sets/dts_1')[0].body).toEqual({ behaviour_key: 'pii' });
  });

  it('the send says whether the behaviour reached miStudio, from the step record', () => {
    const register = SEND.steps.find((s) => s.step === 'register') ?? SEND.steps[0];
    const notSent = { ...SEND, steps: [{ ...register, step: 'register' as const, behaviour: { sent: false, reason: 'not served by miStudio at http://m (read /api/openapi.json)' } }] };
    const { rerender } = render(<SendProgress send={notSent} onResume={() => {}} onCancel={() => {}} />);
    expect(screen.getByTestId('send-behaviour')).toHaveTextContent('Behaviour not sent: not served by miStudio');
    const sent = { ...SEND, steps: [{ ...register, step: 'register' as const, behaviour: { sent: true, reason: 'sent' } }] };
    rerender(<SendProgress send={sent} onResume={() => {}} onCancel={() => {}} />);
    expect(screen.getByTestId('send-behaviour')).toHaveTextContent('Behaviour sent to miStudio.');
  });
});

const BODY = {
  style: 'binary', messages: [{ role: 'user', content: '{text}' }], input_fields: ['text'], axes: [],
  parser: 'verdict_line_v1', allowed_verdicts: ['yes', 'no'], json_schema: null, pair_fields: null, swap_map: null,
};
const RUBRIC: Rubric = {
  id: 'rb_1', name: 'pii/judge', version: 1, ref: 'pii/judge@1', content_hash: 'b'.repeat(64), style: 'binary',
  body: BODY, behaviour_key: null, used: false, created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-21T00:00:00Z',
};

describe('rubric behaviour key', () => {
  it('a new rubric sends its key once', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/rubrics': [],
      'POST /api/v1/rubrics': () => ({ status: 201, json: { ...RUBRIC, behaviour_key: 'pii' } }),
    });
    render(<RubricLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'pii/judge' } });
    fireEvent.change(screen.getByLabelText('Rubric behaviour key'), { target: { value: 'pii' } });
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'Judge.' } });
    fireEvent.change(screen.getByLabelText('Message 2 content'), { target: { value: '{text}' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'yes, no' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));
    await waitFor(() => expect(byCall(calls, 'POST', '/api/v1/rubrics')).toHaveLength(1));
    expect(byCall(calls, 'POST', '/api/v1/rubrics')[0].body).toMatchObject({ name: 'pii/judge', behaviour_key: 'pii' });
  });

  it('an unused rubric changes its key by PATCH; a used one cannot', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/rubrics': [RUBRIC, { ...RUBRIC, id: 'rb_2', ref: 'pii/judge@2', version: 2, used: true, behaviour_key: 'pii' }],
      'PATCH /api/v1/rubrics/rb_1': { ...RUBRIC, behaviour_key: 'pii' },
    });
    render(<RubricLibrary />);
    await screen.findByText('pii/judge@1');
    expect(screen.queryByRole('button', { name: 'Change the behaviour key of pii/judge@2' })).toBeNull();
    expect(screen.getAllByTestId('rubric-behaviour')[1]).toHaveTextContent('fixed');
    fireEvent.click(screen.getByRole('button', { name: 'Change the behaviour key of pii/judge@1' }));
    fireEvent.change(screen.getByLabelText('Behaviour key of pii/judge@1'), { target: { value: 'pii' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(byCall(calls, 'PATCH', '/api/v1/rubrics/rb_1')).toHaveLength(1));
    expect(byCall(calls, 'PATCH', '/api/v1/rubrics/rb_1')[0].body).toEqual({ behaviour_key: 'pii' });
  });
});

describe('session data', () => {
  it('a flagged source shows the badge; an unflagged one can be marked once', async () => {
    const flagged = { ...SOURCE, id: 's-flag', display_name: 'disagreements.jsonl', session_data: true, session_data_basis: 'operator' as const };
    const plain = { ...SOURCE, id: 's-plain', display_name: 'plain.jsonl', session_data: false };
    const { calls } = mockFetch({
      'POST /api/v1/sources/s-plain/session-data': { ...plain, session_data: true, session_data_basis: 'operator' },
      'GET /api/v1/sources': { items: [flagged, plain], total: 2, page: 1, limit: 50 },
    });
    useSourcesStore.setState({ sources: [flagged, plain], loading: false, imports: {} });
    render(<SourcesList onOpen={() => {}} />);
    expect(screen.getAllByTestId('session-data-badge')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Mark plain.jsonl as session data' }));
    await waitFor(() => expect(byCall(calls, 'POST', '/api/v1/sources/s-plain/session-data')).toHaveLength(1));
    expect(byCall(calls, 'POST', '/api/v1/sources/s-plain/session-data')[0].body).toEqual({ session_data: true });
  });

  it('the publish checks show the session-data warning without blocking', () => {
    const run: CheckRun = {
      id: 'pchk_1', version_id: 'v', build_id: 'b', repo_id: 'o/r', requested_visibility: 'public', status: 'completed',
      results: [], licence_table_version: 1, job_id: null, created_at: '', completed_at: '',
      warnings: [{ code: 'session_data', severity: 'amber', message: 'This version is built from recorded user traffic (session data).', sources: [{ source_id: 's', name: 'disagreements.jsonl', basis: 'recognised' }] }],
    };
    render(<ChecksPanel run={run} pending={false} />);
    const w = screen.getByTestId('warning-session_data');
    expect(w).toHaveTextContent('does not block the push');
    expect(w).toHaveTextContent('disagreements.jsonl');
  });
});

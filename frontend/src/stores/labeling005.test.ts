// Feature 005's stores against the real API client and a fetch stub (FTASKS 14.1).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mockFetch } from '@/test/fetchMock';
import { PLAN, run } from '@/test/fixtures005';

import { useLabelingStore } from './labelingStore';
import { useLabelRunsStore } from './labelRunsStore';

const BODY = { input_version_id: 'v1', role: 'classifier' as const, template_id: 'dt_1', question: 'Q?', field_map: { text: 'text' }, threshold_positive: 0.5, threshold_negative: 0.2 };

describe('labelRunsStore', () => {
  beforeEach(() => useLabelRunsStore.setState({ list: [], byId: {}, labels: {}, live: {}, selectedId: null, error: null }));
  afterEach(() => vi.unstubAllGlobals());

  it('fetches runs into list and byId', async () => {
    mockFetch({ 'GET /api/v1/label-runs': { items: [run()], total: 1, page: 1, limit: 50 } });
    await useLabelRunsStore.getState().fetchRuns();
    expect(useLabelRunsStore.getState().list).toHaveLength(1);
    expect(useLabelRunsStore.getState().byId.lr_1.state).toBe('running');
  });

  it('cancel and resume call their routes once and keep the answer', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/label-runs/lr_1/cancel': run({ state: 'cancelled' }),
      'POST /api/v1/label-runs/lr_1/resume': run({ state: 'queued' }),
    });
    await useLabelRunsStore.getState().cancelRun('lr_1');
    expect(useLabelRunsStore.getState().byId.lr_1.state).toBe('cancelled');
    await useLabelRunsStore.getState().resumeRun('lr_1');
    expect(useLabelRunsStore.getState().byId.lr_1.state).toBe('queued');
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/v1/label-runs/lr_1/cancel', 'POST /api/v1/label-runs/lr_1/resume']);
  });

  it('progress events update live counts; a terminal event re-reads the run', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/label-runs/lr_1': run({ state: 'completed' }) });
    useLabelRunsStore.getState().applyEvent('lr_1', 'label_run:progress', { rows_done: 10, rows_total: 20, rows_per_second: 21.2, eta_seconds: 1, counts: { positive: 10 } });
    expect(useLabelRunsStore.getState().live.lr_1.rows_per_second).toBe(21.2);
    useLabelRunsStore.getState().applyEvent('lr_1', 'label_run:completed', {});
    await vi.waitFor(() => expect(useLabelRunsStore.getState().byId.lr_1?.state).toBe('completed'));
    expect(calls).toHaveLength(1);
  });
});

describe('labelingStore', () => {
  beforeEach(() => useLabelingStore.setState({ plan: null, planError: null, approval: null, started: null, error: null, keepShare: null, calibration: {}, tests: {} }));
  afterEach(() => vi.unstubAllGlobals());

  it('a 202 start keeps the approval and no run', async () => {
    mockFetch({ 'POST /api/v1/label-runs': () => ({ status: 202, json: { approval_id: 'apr_1', status: 'pending', action: 'agent_label_rows', hint: 'wait' } }) });
    expect(await useLabelingStore.getState().startRun(BODY)).toBeNull();
    expect(useLabelingStore.getState().approval?.approval_id).toBe('apr_1');
  });

  it('a 201 start keeps the run and sends a completed keep-share job id', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/label-runs': () => ({ status: 201, json: run({ state: 'queued' }) }) });
    useLabelingStore.setState({ keepShare: { job_id: 'job_ks', status: 'completed', share: 0.4, lo: 0.3, hi: 0.5, n: 400, seed: 0, probabilities: [], error: null } });
    expect((await useLabelingStore.getState().startRun(BODY))?.id).toBe('lr_1');
    expect((calls[0].body as Record<string, unknown>).keep_share_job_id).toBe('job_ks');
  });

  it('the plan is fetched with the start body as JSON', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/label-runs/plan': PLAN });
    await useLabelingStore.getState().fetchPlan(BODY);
    expect(useLabelingStore.getState().plan?.rows_to_score).toBe(25000);
    expect(JSON.parse(decodeURIComponent(calls[0].path.split('request=')[1]))).toEqual(BODY);
  });

  it('a failed calibration lookup reads as "none recorded", never as a pass', async () => {
    mockFetch({});
    await useLabelingStore.getState().fetchCalibration('h');
    expect(useLabelingStore.getState().calibration.h.status).toBe('none_recorded');
    expect(useLabelingStore.getState().calibration.h.verdict).toBeNull();
  });

  it('a failed endpoint test is shown with its code', async () => {
    mockFetch({ 'POST /api/v1/endpoint-roles/classifier/test': () => ({ status: 409, json: { error: { code: 'ROLE_UNCONFIGURED', message: 'Set it', details: {} } } }) });
    await useLabelingStore.getState().testEndpoint('classifier');
    expect(useLabelingStore.getState().tests.classifier.error_code).toBe('ROLE_UNCONFIGURED');
  });
});

// Feature 005's screen parts (FTASKS 14.2 – 14.7).
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RoleCapabilityLine } from '@/components/settings/RoleCapabilityLine';
import { useLabelingStore } from '@/stores/labelingStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { mockFetch } from '@/test/fetchMock';
import { PLAN, RUBRIC, TEMPLATE, run } from '@/test/fixtures005';

import { CalibrationCallout } from './CalibrationCallout';
import { BAND_WARNING, KeepShareLine } from './KeepShareLine';
import { LabelRunCard } from './LabelRunCard';
import { LabelRunDetail } from './LabelRunDetail';
import { LabelStep, PLAN_DEBOUNCE_MS, QUESTION_HINT, thresholdsValid } from './LabelStep';
import { bins } from './ProbabilityHistogram';

const VERSIONS = { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'humor', target_type: 'detector', number: 1, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 25000, total_bytes: 1, warnings_count: 0 }], total: 1 };

describe('thresholdsValid (T-19 mirror)', () => {
  it.each([['', '0.2', false], ['0.5', '', false], ['0.2', '0.5', false], ['0.5', '0.5', false], ['1.2', '0.2', false], ['0.5', '0.2', true]])('%s / %s', (p, n, ok) => {
    expect(thresholdsValid(p, n)).toBe(ok);
  });
});

describe('LabelStep', () => {
  beforeEach(() => useLabelingStore.setState({ plan: null, sample: null, keepShare: null, started: null, approval: null, error: null, planError: null, calibration: {} }));
  afterEach(() => vi.unstubAllGlobals());

  const routes = {
    'GET /api/v1/decision-templates': [TEMPLATE],
    'GET /api/v1/rubrics': [RUBRIC],
    'GET /api/v1/versions': VERSIONS,
    'GET /api/v1/label-runs/plan': PLAN,
    'POST /api/v1/labeling/sample': { rows: [{ row_key: 'k1', text: 'a pun', probability: 0.61, distribution: null, outcome: 'positive', verdict: null, rationale: null, latency_ms: 40, error: null }], model: 'JEV-9B-decision', server_kind: 'millm', steering_state: 'unsteered (scoring mode)' },
    'POST /api/v1/label-runs': () => ({ status: 201, json: run({ state: 'queued' }) }),
  };

  async function fill() {
    await waitFor(() => expect(screen.getByRole('option', { name: /humor v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Version to label'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Decision template'), { target: { value: 'dt_1' } });
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Is this text intended to be humorous?' } });
  }

  it('thresholds start empty and the start button stays disabled until both are valid', async () => {
    mockFetch(routes);
    render(<LabelStep />);
    await fill();
    expect(screen.getByLabelText('Positive at or above')).toHaveValue('');
    expect(screen.getByLabelText('Negative at or below')).toHaveValue('');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    expect(screen.getByText(QUESTION_HINT)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Positive at or above'), { target: { value: '0.2' } });
    fireEvent.change(screen.getByLabelText('Negative at or below'), { target: { value: '0.5' } });
    expect(screen.getByRole('alert')).toHaveTextContent('lower than the positive one');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
  });

  it('a valid form plans, names the row count, samples, and starts', async () => {
    const { calls } = mockFetch(routes);
    render(<LabelStep />);
    await fill();
    fireEvent.change(screen.getByLabelText('Positive at or above'), { target: { value: '0.5' } });
    fireEvent.change(screen.getByLabelText('Negative at or below'), { target: { value: '0.2' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByTestId('plan-line')).toHaveTextContent('25,000 to label');
    fireEvent.click(screen.getByRole('button', { name: 'Try it on a sample' }));
    await waitFor(() => expect(within(screen.getByTestId('sample-panel')).getByText('P 0.610')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Label 25,000 rows' }));
    await waitFor(() => expect(screen.getByText(/started; follow it on Label runs/)).toBeInTheDocument());
    const start = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/label-runs');
    expect(start?.body).toMatchObject({ input_version_id: 'v1', role: 'classifier', template_id: 'dt_1', threshold_positive: 0.5, threshold_negative: 0.2, field_map: { text: 'text' } });
  });
});

describe('parts', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('KeepShareLine names its sample size and the warning', () => {
    render(<KeepShareLine estimate={{ share: 0.47, lo: 0.44, hi: 0.5, n: 400 }} actual={0.472} />);
    expect(screen.getByTestId('keep-share')).toHaveTextContent('Keeps 47% [44%, 50%] on a sample of 400 rows');
    expect(screen.getByText(/actual 47.2%/)).toBeInTheDocument();
    expect(screen.getByText(BAND_WARNING)).toBeInTheDocument();
  });

  it.each([
    ['passes', 'pass', 'Calibrated'],
    ['fails', 'fail', 'Not calibrated: this'],
    ['invalid', 'fail', 'latest record is invalid'],
    ['insufficient', 'fail', 'latest record is insufficient'],
    [null, 'none', 'Not calibrated yet'],
  ] as const)('CalibrationCallout reads 006 verdict %s', (verdict, status, text) => {
    render(
      <CalibrationCallout
        status={{ status: verdict ? 'recorded' : 'none_recorded', record_id: verdict ? 'cr_1' : null, verdict, rule: null, auroc: null, calibration_set: null }}
      />,
    );
    expect(screen.getByTestId('calibration-callout')).toHaveAttribute('data-status', status);
    expect(screen.getByTestId('calibration-callout')).toHaveTextContent(text);
  });

  it('LabelRunCard shows kind, model in cyan for miLLM, rows and the unpinned badge', () => {
    render(<LabelRunCard run={run({ pinned: false })} onOpen={() => undefined} selected={false} />);
    const card = screen.getByTestId('label-run-card');
    expect(card).toHaveTextContent('Classifier');
    expect(card).toHaveTextContent('Unpinned');
    expect(card).toHaveTextContent('6 of 25,000 rows');
    expect(screen.getByText('JEV-9B-decision').className).toContain('text-cyan');
  });

  it('LabelRunCard omits rows/s when no rate was measured, and never shows a dash for it', () => {
    render(<LabelRunCard run={run({ state: 'completed' })} onOpen={() => undefined} selected={false} />);
    const card = screen.getByTestId('label-run-card');
    expect(card).not.toHaveTextContent('rows/s');
    expect(card).not.toHaveTextContent('—');
    expect(card).toHaveTextContent('6 of 25,000 rows · chunks of');
  });

  it('LabelRunCard shows the measured rate while one is reported', () => {
    const live = { rows_done: 7, rows_total: 9, rows_per_second: 21.25, eta_seconds: 1, counts: {} };
    render(<LabelRunCard run={run()} live={live} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('label-run-card')).toHaveTextContent('7 of 9 rows · 21.3 rows/s · chunks of');
  });

  it('LabelRunDetail draws provenance and offers Cancel run while live', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 },
      'POST /api/v1/label-runs/lr_1/cancel': run({ state: 'cancelled' }),
    });
    render(<LabelRunDetail run={run()} />);
    expect(screen.getByTestId('provenance')).toHaveTextContent('Ada (operator)');
    expect(screen.getByTestId('provenance')).toHaveTextContent('JEV-9B-decision @ b63f651ce8ed');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
  });

  it('LabelRunDetail offers Resume run on a failed run and shows its reason', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 } });
    render(<LabelRunDetail run={run({ state: 'failed', error: { code: 'LEASE_LOST', message: 'The miLLM lease was lost' } })} />);
    expect(screen.getByRole('button', { name: 'Resume run' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('The miLLM lease was lost');
  });

  it('histogram bins cover [0, 1] in 20 steps', () => {
    const b = bins([0, 0.049, 0.05, 0.999, 1]);
    expect(b).toHaveLength(20);
    expect(b[0].rows).toBe(2);
    expect(b[1].rows).toBe(1);
    expect(b[19].rows).toBe(2);
  });

  it('RoleCapabilityLine tests the endpoint and shows miLLM in cyan', async () => {
    useLabelingStore.setState({ tests: {} });
    mockFetch({ 'POST /api/v1/endpoint-roles/classifier/test': { role: 'classifier', reachable: true, model_listed: true, protocol_ok: true, server_kind: 'millm', resident_model: 'JEV-9B-decision', lease_supported: true, lease_state: 'free', queue: null, error_code: null, message: 'ok' } });
    render(<RoleCapabilityLine role="classifier" />);
    fireEvent.click(screen.getByRole('button', { name: 'Test endpoint' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('miLLM, JEV-9B-decision loaded, lease free'));
  });
});

describe('Label runs screen', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists runs and opens one', async () => {
    useLabelRunsStore.setState({ list: [], byId: {}, selectedId: null, error: null, labels: {}, live: {} });
    mockFetch({
      'GET /api/v1/label-runs': { items: [run()], total: 1, page: 1, limit: 50 },
      'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 },
    });
    const { LabelRunsPanel } = await import('@/components/panels/LabelRunsPanel');
    const { getPanel } = await import('@/config/panels');
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    await waitFor(() => expect(screen.getByTestId('label-run-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('label-run-card'));
    expect(screen.getByTestId('label-run-detail')).toBeInTheDocument();
  });
});

describe('the guided flow mounts the Label step (13.5, P-23)', () => {
  it('discovers guidedStep.label as step "label"', async () => {
    const { discoveredStepPanels, stepsWithoutPanels } = await import('@/components/guided/guidedSteps');
    const panels = discoveredStepPanels();
    expect(stepsWithoutPanels(['label'], panels)).toEqual([]);
    expect(panels.find((p) => p.step === 'label')?.order).toBe(5);
  });
});

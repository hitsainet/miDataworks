// 009: a probe in miLLM as the third labeler on the Label step, and probe-verdict runs on the
// Label runs views. The real store and API client run against a fetch stub; the bodies asserted
// are the ones that leave the browser.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useHealthStore } from '@/stores/healthStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { mockFetch } from '@/test/fetchMock';
import { PLAN, PROBE_LIST, PROBE_PLAN, REPRODUCTION_WILL_RUN, RUBRIC, TEMPLATE, probeRun, run } from '@/test/fixtures005';

import { LabelRunCard } from './LabelRunCard';
import { LabelRunDetail } from './LabelRunDetail';
import { LabelStep, PIN_HINT, PLAN_DEBOUNCE_MS, WINDOW_HINT } from './LabelStep';

const VERSIONS = { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'stakes', target_type: 'detector', number: 1, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 25000, total_bytes: 1, warnings_count: 0 }], total: 1 };

const FORBIDDEN = ['template_id', 'rubric_id', 'question', 'threshold_positive', 'threshold_negative', 'min_top_probability', 'sampling', 'keep_share_job_id'];

function planBody(path: string): unknown {
  return JSON.parse(new URLSearchParams(path.split('?')[1]).get('request') ?? 'null');
}

describe('LabelStep with a probe in miLLM', () => {
  beforeEach(() => useLabelingStore.setState({ plan: null, sample: null, keepShare: null, started: null, approval: null, error: null, planError: null, calibration: {}, probes: null, probesError: null, probesUnconfigured: false, planRefusal: null, link: null, linkError: null, linkApproval: null }));
  afterEach(() => vi.unstubAllGlobals());

  const routes = (plan: unknown = PROBE_PLAN) => ({
    'GET /api/v1/decision-templates': [TEMPLATE],
    'GET /api/v1/rubrics': [RUBRIC],
    'GET /api/v1/versions': VERSIONS,
    'GET /api/v1/labeling/probes': PROBE_LIST,
    'GET /api/v1/label-runs/plan': plan,
    'POST /api/v1/label-runs': () => ({ status: 201, json: probeRun({ state: 'queued' }) }),
  });

  async function chooseProbe() {
    await waitFor(() => expect(screen.getByRole('option', { name: /stakes v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Version to label'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /high-stakes L16 mean/ })).toBeInTheDocument());
  }

  it('offers the probe, fetches miLLM probes and names each one with its layer and model', async () => {
    const { calls } = mockFetch(routes());
    render(<LabelStep />);
    expect(screen.getByRole('option', { name: 'A probe in miLLM' })).toBeInTheDocument();
    await chooseProbe();
    expect(calls.some((c) => c.method === 'GET' && c.path === '/api/v1/labeling/probes')).toBe(true);
    expect(screen.getByRole('option', { name: /high-stakes L16 mean · layer 16 · fitted on meta-llama\/Llama-3.1-8B-Instruct$/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /high-stakes L11 rolling .*\(fitted on LiquidAI\/LFM2.5-1.2B-Instruct — not the loaded model\)/ })).toBeInTheDocument();
    expect(screen.getByLabelText('Window')).toHaveValue('all');
    expect(screen.getByText(WINDOW_HINT)).toBeInTheDocument();
    expect(screen.getByText(PIN_HINT)).toBeInTheDocument();
  });

  it('hides the template, question, thresholds, sample and keep-share inputs', async () => {
    mockFetch(routes());
    render(<LabelStep />);
    await chooseProbe();
    for (const label of ['Decision template', 'Rubric', 'Question', 'Positive at or above', 'Negative at or below', 'Column the template reads']) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Estimate keep share' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try it on a sample' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Column the probe reads')).toHaveValue('text');
  });

  it('plans and starts with exactly the probe body, and shows the probe and reproduction lines', async () => {
    const { calls } = mockFetch(routes());
    render(<LabelStep />);
    await chooseProbe();
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    fireEvent.change(screen.getByLabelText('Window'), { target: { value: 'prompt' } });
    fireEvent.change(screen.getByLabelText('Column the probe reads'), { target: { value: 'prose' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled(), { timeout: PLAN_DEBOUNCE_MS + 2000 });

    const expected = { input_version_id: 'v1', role: 'probe', probe: { probe_id: 'pr_5ac12236c0dd', window: 'prompt' }, field_map: { text: 'prose' } };
    const plans = calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan'));
    expect(planBody(plans[plans.length - 1].path)).toEqual(expected);

    const line = screen.getByTestId('probe-plan-line');
    expect(line).toHaveTextContent('high-stakes L16 mean');
    expect(line).toHaveTextContent('bar 11.914 (probe score, not a probability)');
    expect(line).toHaveTextContent('Evidence: generalizes to unseen tasks; compared with a judge');
    expect(screen.getByTestId('probe-preflight')).toHaveTextContent('score 12.310 against bar 11.914 (which bar it is was not reported) → fires');
    expect(screen.getByTestId('reproduction-line')).toHaveTextContent(
      "Before labeling, scores 1,200 rows of anthropic_balanced and requires an AUROC inside miStudio's [0.874, 0.907] (miStudio reported AUROC 0.891).",
    );

    fireEvent.click(screen.getByRole('button', { name: 'Label 25,000 rows' }));
    await waitFor(() => expect(screen.getByText(/started; follow it on Label runs/)).toBeInTheDocument());
    const start = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/label-runs');
    expect(start?.body).toEqual(expected);
    for (const key of FORBIDDEN) expect(start?.body).not.toHaveProperty(key);
  });

  it('a chat column maps to messages', async () => {
    const { calls } = mockFetch(routes());
    render(<LabelStep />);
    await chooseProbe();
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    fireEvent.change(screen.getByLabelText('The column holds'), { target: { value: 'messages' } });
    fireEvent.change(screen.getByLabelText('Column the probe reads'), { target: { value: 'conversation' } });
    await waitFor(() => expect(screen.getByTestId('probe-plan-line')).toBeInTheDocument(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    const plans = calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan'));
    expect(planBody(plans[plans.length - 1].path)).toMatchObject({ field_map: { messages: 'conversation' } });
  });

  it('shows a provisional bar, an unchecked preflight and a cached reproduction', async () => {
    const plan = {
      ...PROBE_PLAN,
      probe: { ...PROBE_PLAN.probe!, window: 'last_user', window_bar: { threshold: 11.914, provisional: true }, preflight: { checked: false, reason: 'no model loaded in miLLM' } },
      reproduction: { ...REPRODUCTION_WILL_RUN, state: 'passed', cached_from_run_id: 'lr_earlier', millm_auroc: 0.8893, rows_scored: 1200 },
    };
    mockFetch(routes(plan));
    render(<LabelStep />);
    await chooseProbe();
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    await waitFor(() => expect(screen.getByTestId('probe-plan-line')).toBeInTheDocument(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByTestId('probe-plan-line')).toHaveTextContent('provisional: this window has no bar of its own');
    expect(screen.getByTestId('probe-preflight')).toHaveTextContent('Preflight not checked: no model loaded in miLLM');
    expect(screen.getByTestId('reproduction-line')).toHaveTextContent('Reproduction already passed on run lr_earlier');
  });

  it('shows a plan refusal in the server words, verbatim', async () => {
    const message = 'miStudio did not report this probe\'s AUROC on a view this version holds, so the run cannot check that miLLM reproduces it.';
    mockFetch({ ...routes(), 'GET /api/v1/label-runs/plan': () => ({ status: 422, json: { error: { code: 'REPRODUCTION_UNAVAILABLE', message, details: {} } } }) });
    render(<LabelStep />);
    await chooseProbe();
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByText(message)).toHaveAttribute('role', 'alert');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
  });

  it('says why probes are unavailable when miLLM is not configured, in the server words', async () => {
    const message = 'Probe scoring runs on miLLM, and MILLM_BASE_URL is not set. Set it in the deployment configuration (k8s/base/config.yaml), then start the run.';
    mockFetch({ ...routes(), 'GET /api/v1/labeling/probes': () => ({ status: 409, json: { error: { code: 'PROBE_ENDPOINT_UNCONFIGURED', message, details: { setting: 'MILLM_BASE_URL' } } } }) });
    render(<LabelStep />);
    // Before anyone chooses it, the option itself says why.
    await waitFor(() => expect(screen.getByRole('option', { name: 'A probe in miLLM (miLLM not configured)' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByTestId('probes-unavailable')).toHaveTextContent(message));
    expect(screen.getByTestId('probes-unavailable')).toHaveTextContent('Every other labeler works without it.');
  });

  it('when health reports miLLM not configured, the option is disabled and says so', async () => {
    useHealthStore.setState({ health: { dependencies: { millm: { ok: false, reason: 'not configured', configured: false } } } as never });
    mockFetch({ ...routes(), 'GET /api/v1/labeling/probes': () => ({ status: 409, json: { error: { code: 'PROBE_ENDPOINT_UNCONFIGURED', message: 'MILLM_BASE_URL is not set.', details: {} } } }) });
    try {
      render(<LabelStep />);
      const option = await screen.findByRole('option', { name: 'A probe in miLLM (miLLM not configured)' });
      expect(option).toBeDisabled();
    } finally {
      useHealthStore.setState({ health: null });
    }
  });

  it('a failed probe list reads "Probes unavailable" with the reason, never "Loading"', async () => {
    const message = 'MILLM_BASE_URL (http://x) answers as a vllm server, not miLLM.';
    mockFetch({ ...routes(), 'GET /api/v1/labeling/probes': () => ({ status: 409, json: { error: { code: 'PROBE_ENDPOINT_NOT_MILLM', message, details: {} } } }) });
    render(<LabelStep />);
    await waitFor(() => expect(screen.getByRole('option', { name: /stakes v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: `Probes unavailable: ${message}` })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: /Loading probes/ })).not.toBeInTheDocument();
  });

  it('with miLLM unconfigured by the probe list, the placeholder names MILLM_BASE_URL', async () => {
    mockFetch({ ...routes(), 'GET /api/v1/labeling/probes': () => ({ status: 409, json: { error: { code: 'PROBE_ENDPOINT_UNCONFIGURED', message: 'MILLM_BASE_URL is not set.', details: {} } } }) });
    render(<LabelStep />);
    await waitFor(() => expect(screen.getByRole('option', { name: 'A probe in miLLM (miLLM not configured)' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: 'Probes unavailable: miLLM is not configured (MILLM_BASE_URL)' })).toBeInTheDocument());
  });

  it('a probe-list refusal that is not "unconfigured" is shown verbatim and the option stays plain', async () => {
    const message = 'MILLM_BASE_URL (http://x) answers as a vllm server, not miLLM.';
    mockFetch({ ...routes(), 'GET /api/v1/labeling/probes': () => ({ status: 409, json: { error: { code: 'PROBE_ENDPOINT_NOT_MILLM', message, details: {} } } }) });
    render(<LabelStep />);
    await waitFor(() => expect(screen.getByRole('option', { name: /stakes v1/ })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'A probe in miLLM' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument());
  });

  it('a classifier plan never carries probe lines', async () => {
    mockFetch(routes(PLAN));
    render(<LabelStep />);
    await waitFor(() => expect(screen.getByRole('option', { name: /stakes v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Version to label'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Decision template'), { target: { value: 'dt_1' } });
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'q' } });
    fireEvent.change(screen.getByLabelText('Positive at or above'), { target: { value: '0.5' } });
    fireEvent.change(screen.getByLabelText('Negative at or below'), { target: { value: '0.2' } });
    await waitFor(() => expect(screen.getByTestId('plan-line')).toBeInTheDocument(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.queryByTestId('probe-plan-line')).not.toBeInTheDocument();
    expect(screen.queryByTestId('reproduction-line')).not.toBeInTheDocument();
  });
});

describe('probe-verdict runs on the Label runs views', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('the card names the kind, the probe and window, and the unpinned badge', () => {
    render(<LabelRunCard run={probeRun()} onOpen={() => undefined} selected={false} />);
    const card = screen.getByTestId('label-run-card');
    expect(card).toHaveTextContent('Probe verdict');
    expect(card).toHaveTextContent('probe pr_5ac12236c0dd · window all');
    expect(card).toHaveTextContent('Unpinned');
  });

  it('the card reads unpinned from the snapshot when the run column is unset', () => {
    render(<LabelRunCard run={probeRun({ pinned: null })} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('label-run-card')).toHaveTextContent('Unpinned');
  });

  it('the detail shows the probe, the unpinned reason and the reproduction record', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_p/labels': { items: [], total: 0, page: 1, limit: 500 } });
    render(<LabelRunDetail run={probeRun()} />);
    const block = screen.getByTestId('probe-run-block');
    expect(block).toHaveTextContent('Probe pr_5ac12236c0dd · window all');
    expect(block).toHaveTextContent('Unpinned: miLLM does not report a model revision');
    expect(within(block).getByTestId('reproduction-line')).toHaveTextContent(
      "Reproduction passed: miLLM AUROC 0.889 on 1,200 rows against miStudio's AUROC 0.891, interval [0.874, 0.907].",
    );
    expect(screen.getByTestId('provenance')).toHaveTextContent('pr_5ac12236c0dd · window all · layer 16');
    expect(screen.queryByTestId('rederive')).not.toBeInTheDocument();
  });

  it('a failed reproduction says so with its reason', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_p/labels': { items: [], total: 0, page: 1, limit: 500 } });
    const failed = probeRun({ state: 'failed' });
    failed.endpoint_snapshot.reproduction = { ...failed.endpoint_snapshot.reproduction!, state: 'failed', millm_auroc: 0.61, reason: 'miLLM AUROC is outside miStudio\'s interval' };
    render(<LabelRunDetail run={failed} />);
    const line = within(screen.getByTestId('probe-run-block')).getByTestId('reproduction-line');
    expect(line).toHaveAttribute('data-state', 'failed');
    expect(line).toHaveTextContent('Reproduction failed: miLLM AUROC 0.610');
    expect(line).toHaveTextContent("outside miStudio's interval");
  });

  it('a classifier run shows no probe block', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 } });
    render(<LabelRunDetail run={run()} />);
    expect(screen.queryByTestId('probe-run-block')).not.toBeInTheDocument();
  });
});

// The 11 findings of the novice re-walk (2026-10-19, 001 FTASKS 16.8 and 005 FTASKS 17.6; record in
// 0xcc/reviews/live2_2026-10-17_controls.md), one describe per finding. Components run their real
// stores and API client against fetch stubs.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ActiveOperations } from '@/components/operations/ActiveOperations';
import { AgreementBlock } from '@/components/labeling/AgreementBlock';
import { LabelRunCard } from '@/components/labeling/LabelRunCard';
import { LabelStep, PLAN_DEBOUNCE_MS, RESIDENT_POLL_MS, isEverydayVersion } from '@/components/labeling/LabelStep';
import { probeOptionText, probeTags } from '@/components/labeling/probeChoice';
import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { ImportForm } from '@/components/sources/ImportForm';
import { NOT_STATED_ADVICE } from '@/components/sources/LicenceBadge';
import { getPanel } from '@/config/panels';
import { useDraftsStore } from '@/stores/draftsStore';
import { useHealthStore } from '@/stores/healthStore';
import { useJobsStore } from '@/stores/jobsStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { Health } from '@/types/api';
import type { LabelAgreement, ProbeListItem } from '@/types/labeling';
import type { RecipeDraft } from '@/types/recipes';
import { formatEt } from '@/utils/format';

import { HEALTH, job, mockFetch } from './fetchMock';
import { DETAIL, PREVIEW, SOURCE } from './fixtures001';
import { VERSION } from './fixtures002';
import { PROBE_LIST, PROBE_PLAN, TEMPLATE, probeRun, run } from './fixtures005';

const WAIT = { timeout: PLAN_DEBOUNCE_MS + 3000 };
const LLAMA = 'meta-llama/Llama-3.1-8B-Instruct';
const V1 = { ...VERSION, id: 'v1', dataset_name: 'colbert-humor-detection', number: 1, total_rows: 200000, warnings_count: 0 };
const HUMOR = { 'humorous': 'positive', 'not_humorous': 'negative' };
const OLD: ProbeListItem = {
  ...PROBE_LIST.items[0], probe_id: 'pr_7de1e85e2d16', name: 'pmr_59980a0fb155 L12', layer: 12, label_mapping: HUMOR, concept: null,
  threshold: 101.25, created_at: '2026-10-01T16:00:00Z', trained_at: '2026-09-30T22:05:00Z', train_dataset: 'CreativeLang/ColBERT_Humor_Detection (train)',
  mistudio_run_id: 'pmr_59980a0fb155', auroc: 0.749, auroc_dataset: 'CreativeLang/ColBERT_Humor_Detection (test)', target_fpr: 0.01,
};
const NEW: ProbeListItem = {
  ...OLD, probe_id: 'pr_a1993af56691', name: 'pmr_a1993af56691 L12', created_at: '2026-10-18T13:00:00Z', trained_at: '2026-10-18T12:00:00Z',
  mistudio_run_id: 'pmr_a1993af56691', auroc: 0.712,
};
const NOT_LOADED = (needed = LLAMA) => ({
  status: 409, json: { error: { code: 'MODEL_NOT_LOADED', message: `No model is loaded in miLLM. Load ${needed} (the model probe pr_7de1e85e2d16 was fitted on), then start the run.`, details: { needed, resident: null } } },
});
const health = (millm: Partial<Health['dependencies']['millm']> = {}): Health =>
  ({ ...HEALTH, dependencies: { ...HEALTH.dependencies, millm: { ...HEALTH.dependencies.millm, ...millm } } }) as unknown as Health;

type Answer = { status?: number; json?: unknown };
const labelRoutes = (plan: Answer | (() => Answer), probes: unknown = { ...PROBE_LIST, items: [OLD, NEW], resident_model: null }) => ({
  'GET /api/v1/decision-templates': [TEMPLATE],
  'GET /api/v1/rubrics': [],
  'GET /api/v1/versions': { items: [V1], total: 1 },
  'GET /api/v1/labeling/probes': probes,
  'GET /api/v1/label-runs/plan': typeof plan === 'function' ? plan : () => plan,
  'GET /api/v1/calibration-status': () => ({ status: 404, json: { error: { code: 'NOT_FOUND', message: 'none', details: {} } } }),
});

/** The probe list as miLLM answers it: nothing loaded until `loaded()` turns true. */
const residentWhen = (loaded: () => boolean) => () => ({ json: { ...PROBE_LIST, items: [OLD, NEW], resident_model: loaded() ? LLAMA : null } });

async function chooseProbe(id = OLD.probe_id) {
  fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
  await waitFor(() => expect(within(screen.getByLabelText('Probe')).getAllByRole('option').length).toBeGreaterThan(1));
  fireEvent.change(screen.getByLabelText('Probe'), { target: { value: id } });
}

const DRAFT: RecipeDraft = {
  id: 'draft-1', recipe_id: 'rec-1', dataset_id: 'd-1', name: null, body: {}, step_labels: [], inputs: [],
  flow_state: { step: 'label', choices: { version_id: 'v1' } }, updated_by: 'Ada', updated_at: '',
};
const labelStep = () => discoveredStepPanels().find((p) => p.step === 'label')!.Component;

beforeEach(() => {
  useLabelingStore.setState({ plan: null, planError: null, planErrorCode: null, planErrorDetails: {}, planRefusal: null, probes: null, probesError: null, probesUnconfigured: false, started: null, approval: null, error: null, calibration: {} });
  useHealthStore.setState({ health: health() });
  useLabelRunsStore.setState({ list: [], byId: {}, live: {}, labels: {}, selectedId: null, error: null });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useDraftsStore.setState({ draft: null });
  useVersionsStore.setState({ versionList: [] });
});

describe('finding 1: the Label step notices a model loaded in miLLM, and keeps its choices', () => {
  it('re-reads miLLM when the tab regains focus and the start unblocks without a reload', async () => {
    let loaded = false;
    const { calls } = mockFetch(labelRoutes(() => (loaded ? { json: { ...PROBE_PLAN, rows_to_score: 200000, rows_total: 200000 } } : NOT_LOADED()), residentWhen(() => loaded)));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('model-wait')).toHaveTextContent(`Load ${LLAMA} in miLLM.`), WAIT);
    loaded = true; // the operator loads the model in miLLM's own UI
    const before = calls.filter((c) => c.path.startsWith('/api/v1/labeling/probes')).length;
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 200,000 rows' })).toBeEnabled(), WAIT);
    expect(calls.filter((c) => c.path.startsWith('/api/v1/labeling/probes')).length).toBeGreaterThan(before);
    expect(screen.queryByTestId('model-wait')).toBeNull();
    expect(screen.getByLabelText('Label with')).toHaveValue('probe');
    expect(screen.getByLabelText('Probe')).toHaveValue(OLD.probe_id);
  });

  it('re-reads miLLM on a timer while it waits, and stops once the model is there', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let loaded = false;
    const { calls } = mockFetch(labelRoutes(() => (loaded ? { json: PROBE_PLAN } : NOT_LOADED()), residentWhen(() => loaded)));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('model-wait')).toBeInTheDocument(), WAIT);
    const plans = () => calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan')).length;
    const first = plans();
    loaded = true;
    await act(() => vi.advanceTimersByTimeAsync(RESIDENT_POLL_MS + 10));
    await waitFor(() => expect(plans()).toBeGreaterThan(first));
    await waitFor(() => expect(screen.queryByTestId('model-wait')).toBeNull());
    const settled = plans();
    await act(() => vi.advanceTimersByTimeAsync(RESIDENT_POLL_MS * 3));
    expect(plans()).toBe(settled); // no polling once nothing is awaited
  });

  it.each([
    ['MODEL_NOT_LOADED', null],
    ['MODEL_NOT_RESIDENT', 'google/gemma-2-2b-it'],
  ])('a plan refused with %s waits for the model even when the probe list says one is loaded', async (code, resident) => {
    const refused = () => ({ status: 409, json: { error: { code, message: `Load ${LLAMA} in miLLM, then start the run.`, details: { needed: LLAMA, resident } } } });
    mockFetch(labelRoutes(refused, { ...PROBE_LIST, items: [OLD, NEW], resident_model: resident ?? LLAMA }));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('model-wait')).toHaveTextContent(`Load ${LLAMA} in miLLM.`), WAIT);
  });

  it('"Check again" re-reads miLLM at once', async () => {
    const { calls } = mockFetch(labelRoutes(() => NOT_LOADED()));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('model-wait')).toBeInTheDocument(), WAIT);
    const before = calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan')).length;
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan')).length).toBe(before + 1));
  });

  it('the guided step keeps the labeler and the probe on the draft, and a reload starts from them', async () => {
    const saved: unknown[] = [];
    mockFetch({ ...labelRoutes(() => NOT_LOADED()), 'PUT /api/v1/recipe-drafts/draft-1': (body: unknown) => { saved.push(body); return { json: { ...DRAFT, ...(body as object) } }; } });
    useDraftsStore.setState({ draft: DRAFT });
    const Step = labelStep();
    const { unmount } = render(<Step draft={DRAFT} onAdvance={vi.fn()} />);
    await chooseProbe();
    fireEvent.change(screen.getByLabelText('Rows to label'), { target: { value: '25000' } });
    const label = () => (useDraftsStore.getState().draft?.flow_state.choices as { label?: Record<string, unknown> }).label;
    await waitFor(() => expect(label()).toMatchObject({ role: 'probe', probe_id: OLD.probe_id, row_cap: '25000', window: 'all', column: 'text' }));
    expect((useDraftsStore.getState().draft?.flow_state.choices as { version_id?: string }).version_id).toBe('v1'); // kept
    unmount();
    // a reload: the draft comes back from the server with the choices on it
    const reloaded = useDraftsStore.getState().draft!;
    render(<Step draft={reloaded} onAdvance={vi.fn()} />);
    expect(screen.getByLabelText('Label with')).toHaveValue('probe');
    await waitFor(() => expect(screen.getByLabelText('Probe')).toHaveValue(OLD.probe_id));
    expect(screen.getByLabelText('Rows to label')).toHaveValue('25000');
  });
});

describe('finding 2: probes that detect the same thing are told apart', () => {
  it('marks the highest AUROC recommended and the latest import newest, and states the facts', () => {
    const lone = { ...PROBE_LIST.items[0] };
    const tags = probeTags([OLD, NEW, lone]);
    expect(tags[OLD.probe_id]).toEqual(['recommended']);
    expect(tags[NEW.probe_id]).toEqual(['newest']);
    expect(tags[lone.probe_id]).toBeUndefined(); // alone in its group: nothing to choose between
    const text = probeOptionText(OLD, tags[OLD.probe_id]);
    expect(text).toContain('humorous vs not_humorous · Llama-3.1-8B-Instruct, layer 12');
    expect(text).toContain('AUROC 0.749');
    expect(text).toContain(`trained ${formatEt(OLD.trained_at)}`);
    expect(text).toContain('recommended');
    expect(probeOptionText(NEW, tags[NEW.probe_id])).not.toEqual(text.replace('recommended', 'newest'));
  });

  it('times read in US Eastern', () => {
    expect(formatEt('2026-09-30T22:05:00Z')).toBe('Sep 30, 2026, 6:05 PM ET');
    expect(formatEt(null)).toBeNull();
  });

  it('the picker names both, and the chosen one\'s training and evaluation show under it', async () => {
    mockFetch(labelRoutes({ json: PROBE_PLAN }, { ...PROBE_LIST, items: [OLD, NEW] }));
    render(<LabelStep versionId="v1" />);
    await chooseProbe(NEW.probe_id);
    const options = within(screen.getByLabelText('Probe')).getAllByRole('option').map((o) => o.textContent ?? '');
    expect(options.find((o) => o.includes(OLD.name))).toContain('recommended');
    expect(options.find((o) => o.includes(NEW.name))).toContain('newest');
    const facts = screen.getByTestId('probe-facts');
    expect(facts).toHaveTextContent('AUROC 0.712 on CreativeLang/ColBERT_Humor_Detection (test)');
    expect(facts).toHaveTextContent(`Trained on CreativeLang/ColBERT_Humor_Detection (train) ${formatEt(NEW.trained_at)}, run pmr_a1993af56691.`);
    expect(facts).toHaveTextContent('Fires at a score of 101.25 or more, set for 1% false positives.');
    expect(facts).toHaveTextContent('2 probes detect the same thing on this model and layer');
  });
});

describe('finding 3: the "load the model" message links to miLLM', () => {
  it('links to the Models page at the configured miLLM UI address', async () => {
    useHealthStore.setState({ health: health({ ui_url: 'https://millm.example.test/' }) });
    mockFetch(labelRoutes(() => NOT_LOADED()));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    const link = await screen.findByTestId('millm-models-link', {}, WAIT);
    expect(link).toHaveAttribute('href', 'https://millm.example.test/models');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('without a configured address it names the setting, and writes no host of its own', async () => {
    mockFetch(labelRoutes(() => NOT_LOADED()));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('model-wait')).toHaveTextContent('MILLM_UI_URL'), WAIT);
    expect(screen.queryByTestId('millm-models-link')).toBeNull();
  });
});

describe('finding 4: progress is rows done of total, in the drawer and on the Label step', () => {
  it('a job:progress event with rows sets the drawer\'s bar and text; a stale poll does not move it back', async () => {
    const labelJob = job({ id: 'job_lr', kind: 'label_run', progress: 0, room: 'dataworks/label-runs/job_lr' });
    useJobsStore.setState({ active: [labelJob as never], failed: [] });
    act(() => useJobsStore.getState().applyEvent({ job_id: 'job_lr', rows_done: 16400, rows_total: 25000 }));
    render(<ActiveOperations />);
    expect(screen.getByTestId('job-progress-text')).toHaveTextContent('16,400 of 25,000 rows · 66% of the job');
    expect(screen.getByRole('progressbar', { name: 'label_run progress' })).toHaveAttribute('aria-valuenow', String((100 * 16400) / 25000));
    // the row, written at most once a minute, still says 61.6%
    mockFetch({ 'GET /api/v1/jobs': { jobs: [{ ...labelJob, progress: 61.6 }] } });
    await act(() => useJobsStore.getState().fetchJobs());
    expect(screen.getByTestId('job-progress-text')).toHaveTextContent('16,400 of 25,000 rows · 66% of the job');
    // once the row is ahead, the row wins
    mockFetch({ 'GET /api/v1/jobs': { jobs: [{ ...labelJob, progress: 80 }] } });
    await act(() => useJobsStore.getState().fetchJobs());
    expect(screen.getByTestId('job-progress-text')).toHaveTextContent('80% of the job');
  });

  it('after Start, the guided step stays on Label, shows the run\'s rows and continues on request', async () => {
    const started = probeRun({ id: 'lr_new', state: 'running', rows_done: 0, rows_total: 25000, input_version_id: 'v1' });
    mockFetch({
      ...labelRoutes({ json: { ...PROBE_PLAN, rows_to_score: 25000 } }, { ...PROBE_LIST, items: [OLD] }),
      'POST /api/v1/label-runs': () => ({ json: started, status: 201 }),
      'GET /api/v1/label-runs/lr_new': { ...started, rows_done: 1200 },
      'PUT /api/v1/recipe-drafts/draft-1': (body: unknown) => ({ json: { ...DRAFT, ...(body as object) } }),
    });
    useDraftsStore.setState({ draft: DRAFT });
    const onAdvance = vi.fn();
    const Step = labelStep();
    const { rerender } = render(<Step draft={DRAFT} onAdvance={onAdvance} />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled(), WAIT);
    fireEvent.click(screen.getByRole('button', { name: 'Label 25,000 rows' }));
    await waitFor(() => expect((useDraftsStore.getState().draft?.flow_state.choices as { label_run_id?: string }).label_run_id).toBe('lr_new'));
    expect(onAdvance).not.toHaveBeenCalled();
    rerender(<Step draft={useDraftsStore.getState().draft!} onAdvance={onAdvance} />);
    await waitFor(() => expect(screen.getByTestId('run-progress-rows')).toHaveTextContent('1,200 of 25,000 rows labeled (4.8%)'));
    // a second run cannot be started over the live one
    expect(screen.getByTestId('start-blockers')).toHaveTextContent('The run above is still labeling');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    act(() => useLabelRunsStore.getState().applyEvent('lr_new', 'label_run:progress', { rows_done: 16400, rows_total: 25000, rows_per_second: 21.5, eta_seconds: 400, counts: {} }));
    expect(screen.getByTestId('run-progress-rows')).toHaveTextContent('16,400 of 25,000 rows labeled (65.6%) · 21.5 rows/s · about 6m 40s left');
    fireEvent.click(screen.getByRole('button', { name: 'Continue to Assemble' }));
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });
});

describe('finding 5\'s class of defect on the Label step: one click starts one run', () => {
  it('a double click on the start button posts one run', async () => {
    const started = probeRun({ id: 'lr_new', state: 'queued', rows_done: 0 });
    const { calls } = mockFetch({
      ...labelRoutes({ json: { ...PROBE_PLAN, rows_to_score: 25000 } }, { ...PROBE_LIST, items: [OLD] }),
      'POST /api/v1/label-runs': () => ({ json: started, status: 201 }),
    });
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    const start = await screen.findByRole('button', { name: 'Label 25,000 rows' }, WAIT);
    await waitFor(() => expect(start).toBeEnabled(), WAIT);
    // both clicks land before React re-renders the button as disabled
    act(() => {
      start.click();
      start.click();
    });
    await waitFor(() => expect(useLabelingStore.getState().started?.id).toBe('lr_new'));
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')).toHaveLength(1);
  });
});

describe('finding 5: one click imports once', () => {
  it('"Import the previewed dataset" posts one import, and the preview\'s other buttons post none', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/sources/hf/preview': PREVIEW, 'POST /api/v1/sources/hf': { job_id: 'job_9', source_id: null, existing_job: false }, 'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 50 } });
    render(<ImportForm />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: PREVIEW.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    const modal = await screen.findByTestId('preview-modal');
    expect(modal).toBeInTheDocument();
    for (const b of screen.getAllByRole('button').filter((el) => el.closest('[role="dialog"], .fixed'))) expect(b).toHaveAttribute('type', 'button');
    fireEvent.click(screen.getByRole('button', { name: 'Import the previewed dataset' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/sources/hf')).toHaveLength(1));
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/sources/hf')).toHaveLength(1);
  });

  it('two submits before the first answers post once', async () => {
    let answer: (v: Response) => void = () => undefined;
    const fetch = vi.fn((input: RequestInfo | URL) => (String(input).includes('/sources/hf') ? new Promise<Response>((r) => { answer = r; }) : Promise.resolve(new Response('{"items":[],"total":0}'))));
    vi.stubGlobal('fetch', fetch);
    const { container } = render(<ImportForm />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: SOURCE.repo_id } });
    const form = container.querySelector('form')!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(fetch.mock.calls.filter(([u]) => String(u).endsWith('/api/v1/sources/hf'))).toHaveLength(1);
    await act(async () => answer(new Response(JSON.stringify({ job_id: 'job_9', source_id: null, existing_job: false }), { status: 202 })));
  });

  it('"Close the preview" closes it and imports nothing', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/sources/hf/preview': PREVIEW });
    render(<ImportForm />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: PREVIEW.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Close the preview' }));
    await waitFor(() => expect(screen.queryByTestId('preview-modal')).toBeNull());
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/sources/hf')).toHaveLength(0);
  });
});

const AGREEMENT: LabelAgreement = {
  label_run_id: 'lr_p', run_state: 'completed', applicable: true, reason: null,
  columns: [{ name: 'humor', values: ['false', 'true'] }, { name: 'kind', values: ['fact', 'joke'] }],
  column: 'humor', values: ['false', 'true'], positive_value: 'true', needs_positive_value: false,
  rows_compared: 25000, rows_not_compared: 0,
  counts: { both_positive: 2500, run_only_positive: 463, column_only_positive: 10000, both_negative: 12037 },
  accuracy: 0.5815, run_positive_share: 0.11852, column_positive_share: 0.5, auroc: 0.748, auroc_from: 'probe score',
  note: "The run marked 11.9% of the 25,000 compared rows positive; the column 'humor' marks 50.0% of them positive. They agree on 58.1%. That is weak agreement.",
};

describe('finding 6: a finished run is compared with the label column the data already has', () => {
  it('shows the note, the four counts and the figures, and lets the reader choose another column', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/label-runs/lr_p/agreement': () => {
        const last = calls.at(-1)!.path;
        if (last.includes('positive_value=joke')) return { json: { ...AGREEMENT, column: 'kind', values: ['fact', 'joke'], positive_value: 'joke', note: 'kind compared.' } };
        if (last.includes('column=kind')) return { json: { ...AGREEMENT, column: 'kind', values: ['fact', 'joke'], applicable: false, needs_positive_value: true, positive_value: null, counts: null, note: null, reason: "Say which of column 'kind''s values (fact or joke) means positive." } };
        return { json: AGREEMENT };
      },
    });
    render(<AgreementBlock runId="lr_p" state="completed" />);
    expect(await screen.findByTestId('agreement-note')).toHaveTextContent('11.9%');
    const counts = screen.getByTestId('agreement-counts');
    expect(counts).toHaveTextContent('2,500');
    expect(counts).toHaveTextContent('10,000');
    expect(screen.getByTestId('agreement-figures')).toHaveTextContent('agree 58.1% · run positive 11.9% · column positive 50.0% · AUROC 0.748 from the probe score');
    fireEvent.change(screen.getByLabelText('Label column to compare with'), { target: { value: 'kind' } });
    expect(await screen.findByTestId('agreement-reason')).toHaveTextContent('means positive');
    fireEvent.change(screen.getByLabelText('Value that means positive'), { target: { value: 'joke' } });
    await waitFor(() => expect(screen.getByTestId('agreement-note')).toHaveTextContent('kind compared.'));
    expect(calls.map((c) => c.path)).toEqual([
      '/api/v1/label-runs/lr_p/agreement',
      '/api/v1/label-runs/lr_p/agreement?column=kind',
      '/api/v1/label-runs/lr_p/agreement?column=kind&positive_value=joke',
    ]);
  });

  it('a version without a label column says so in words', async () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_p/agreement': { ...AGREEMENT, applicable: false, columns: [], column: null, counts: null, note: null, reason: 'The version has no column with exactly two values to compare the run with.' } });
    render(<AgreementBlock runId="lr_p" state="completed" />);
    expect(await screen.findByTestId('agreement-reason')).toHaveTextContent('no column with exactly two values');
    expect(screen.queryByLabelText('Label column to compare with')).toBeNull();
  });

  it('the finished run on the Label step and in Label runs both carry the comparison', async () => {
    const done = probeRun({ id: 'lr_p', state: 'completed', input_version_id: 'v1' });
    mockFetch({ 'GET /api/v1/label-runs/lr_p': done, 'GET /api/v1/label-runs/lr_p/agreement': AGREEMENT, 'GET /api/v1/label-runs/lr_p/labels': { items: [], total: 0, page: 1, limit: 500 } });
    const { LabelRunDetail } = await import('@/components/labeling/LabelRunDetail');
    render(<LabelRunDetail run={done} />);
    expect(await screen.findByTestId('agreement-note')).toBeInTheDocument();
  });
});

describe('finding 7: the blocked start button names the count', () => {
  it('the version\'s rows, or the cap, before the plan answers', async () => {
    mockFetch(labelRoutes(() => NOT_LOADED()));
    render(<LabelStep versionId="v1" />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 200,000 rows' })).toBeDisabled(), WAIT);
    fireEvent.change(screen.getByLabelText('Rows to label'), { target: { value: '25000' } });
    expect(screen.getByRole('button', { name: 'Label 25,000 rows' })).toBeDisabled();
  });
});

describe('finding 8 and 10: the guided Import step says when it reused a source, and what no licence means', () => {
  const importStep = () => discoveredStepPanels().find((p) => p.step === 'import')!.Component;
  const draft: RecipeDraft = { id: 'd1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'import' }, updated_by: 'Ada', updated_at: '' };
  const unlicensed = { ...DETAIL, licence: { ...DETAIL.licence, raw: null, display: 'not stated', origin: null } };

  it('an import answered with the existing source says "Already imported" and the licence advice', async () => {
    mockFetch({ 'POST /api/v1/sources/hf': unlicensed, 'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 50 } });
    const Step = importStep();
    render(<Step draft={draft} onAdvance={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: SOURCE.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(await screen.findByTestId('already-imported')).toHaveTextContent('Already imported: nothing was downloaded');
    expect(screen.getByTestId('import-licence')).toHaveTextContent(NOT_STATED_ADVICE);
  });

  it('a job that finished on an existing source says so too', async () => {
    mockFetch({
      'POST /api/v1/sources/hf': { job_id: 'job_7', source_id: null, existing_job: false },
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 50 },
      [`GET /api/v1/sources/${SOURCE.id}`]: DETAIL,
    });
    const Step = importStep();
    render(<Step draft={draft} onAdvance={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: SOURCE.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(useSourcesStore.getState().imports.job_7).toBeDefined());
    act(() => useSourcesStore.getState().applyImportEvent('source_import:completed', { job_id: 'job_7', source_id: SOURCE.id, existing: true }));
    expect(await screen.findByTestId('already-imported')).toBeInTheDocument();
    expect(screen.getByTestId('import-licence')).not.toHaveTextContent(NOT_STATED_ADVICE); // a stated licence
  });

  it('the preview states the advice when the licence is not stated', async () => {
    mockFetch({ 'POST /api/v1/sources/hf/preview': { ...PREVIEW, licence: { ...PREVIEW.licence, raw: null, display: 'not stated' } } });
    render(<ImportForm />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: PREVIEW.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(NOT_STATED_ADVICE)).toBeInTheDocument();
    expect(NOT_STATED_ADVICE).toMatch(/publish versions built from it privately/);
  });
});

describe('finding 9: label runs are named by dataset and version, and the screen says probes are listed', () => {
  it('the card names the version, and falls back to its id on an older payload', () => {
    const { rerender } = render(<LabelRunCard run={run({ input_dataset_name: 'colbert-humor-detection', input_version_number: 1 })} onOpen={vi.fn()} selected={false} />);
    expect(screen.getByTestId('run-version')).toHaveTextContent('colbert-humor-detection v1');
    expect(screen.getByTestId('run-version')).not.toHaveTextContent('version v-123456');
    rerender(<LabelRunCard run={run()} onOpen={vi.fn()} selected={false} />);
    expect(screen.getByTestId('run-version')).toHaveTextContent('version v-123456');
  });

  it('the subtitle names probe runs', () => {
    expect(getPanel('label-runs').subtitle).toMatch(/probe runs/);
  });
});

describe('finding 11: older and test versions are folded away', () => {
  const head = (id: string, name: string, extra: object = {}) => ({ ...VERSION, id, dataset_name: name, number: 2, warnings_count: 0, ...extra });
  it('classifies versions', () => {
    expect(isEverydayVersion(head('a', 'colbert-humor-detection'))).toBe(true);
    expect(isEverydayVersion(head('b', 'colbert-humor-detection', { is_head: false }))).toBe(false);
    expect(isEverydayVersion(head('c', 'e2e-test-7'))).toBe(false);
    expect(isEverydayVersion(head('d', 'smoke_run'))).toBe(false);
    expect(isEverydayVersion(head('e', 'contest-entries'))).toBe(true); // "test" inside a word is not a test build
  });

  it('shows heads by default, the journey\'s version always, and the rest behind a toggle', async () => {
    const items = [
      head('v-head', 'humor'),
      head('v-old', 'humor', { number: 1, is_head: false }),
      head('v-test', 'e2e-test-3'),
      head('v-mine', 'novice-humor', { is_head: false }),
    ];
    mockFetch({ 'GET /api/v1/decision-templates': [], 'GET /api/v1/rubrics': [], 'GET /api/v1/labeling/probes': PROBE_LIST, 'GET /api/v1/versions': { items, total: items.length } });
    render(<LabelStep versionId="v-mine" />);
    const ids = () => within(screen.getByLabelText('Version to label')).getAllByRole('option').map((o) => (o as HTMLOptionElement).value).filter(Boolean);
    await waitFor(() => expect(ids()).toEqual(['v-head', 'v-mine']));
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 older and test versions' }));
    expect(ids()).toEqual(['v-head', 'v-old', 'v-test', 'v-mine']);
    fireEvent.click(screen.getByRole('button', { name: 'Hide older and test versions' }));
    expect(ids()).toEqual(['v-head', 'v-mine']);
  });
});

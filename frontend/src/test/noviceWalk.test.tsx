// The 14 findings of the novice walk (2026-10-19, /tmp/claude-1000/e2e-novice/RECORD.md), one
// describe per finding. Components run their real stores and API client against fetch stubs.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { HelpLink } from '@/components/help/HelpLink';
import { FailedOperations, splitFailures } from '@/components/operations/FailedOperations';
import { LabelRunCard } from '@/components/labeling/LabelRunCard';
import { LabelStep, PLAN_DEBOUNCE_MS, parseRowCap } from '@/components/labeling/LabelStep';
import { probeSummaryLines } from '@/components/labeling/ProbeLines';
import { RecipesPanel } from '@/components/panels/RecipesPanel';
import { RecipeEditor } from '@/components/recipes/RecipeEditor';
import { resetOperatorChoices } from '@/components/recipes/operatorChoices';
import { draftPatchFor, suggestedDatasetName } from '@/components/sources/handoff';
import { ImportForm } from '@/components/sources/ImportForm';
import { SourceDetailDrawer } from '@/components/sources/SourceDetailDrawer';
import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { HELP_TOPIC_IDS } from '@/config/help';
import { useDraftsStore } from '@/stores/draftsStore';
import { useJobsStore } from '@/stores/jobsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { Job } from '@/types/api';
import type { RecipeDraft } from '@/types/recipes';
import { formatBytes } from '@/utils/format';

import { HEALTH, job, mockFetch } from './fetchMock';
import { DETAIL, META as SOURCES_META, SOURCE } from './fixtures001';
import { META, VERSION } from './fixtures002';
import { CURATE_LIST, DEDUP_EXACT, SAMPLE_OP } from './fixtures003';
import { PLAN, PROBE_LIST, PROBE_PLAN, REPRODUCTION_WILL_RUN, TEMPLATE, run } from './fixtures005';

const WAIT = { timeout: PLAN_DEBOUNCE_MS + 2000 };
const DRAFT: RecipeDraft = {
  id: 'draft-1', recipe_id: null, dataset_id: 'd-1', name: null, body: {}, step_labels: [], inputs: [{ kind: 'source', source_id: SOURCE.id }],
  flow_state: { step: 'curate', choices: { dataset_name: 'novice-humor' } }, updated_by: 'Ada', updated_at: '',
};
const VALID = (n: number) => ({ valid: true, body_errors: [], steps: Array.from({ length: n }, (_, i) => ({ index: i + 1, operator: 'sample', version: '1.0.0', errors: [] })) });

beforeEach(() => resetOperatorChoices());
afterEach(() => {
  vi.unstubAllGlobals();
  useDraftsStore.setState({ draft: null, validation: null, validatedKey: null, error: null });
});

const curateStep = () => discoveredStepPanels().find((p) => p.step === 'curate')!.Component;

describe('finding 1: the Curate step adds, configures and checks steps', () => {
  const routes = (validation: unknown = VALID(1)) => ({
    'GET /api/v1/operators': CURATE_LIST,
    'GET /api/v1/operators/sample/1.0.0': SAMPLE_OP,
    'GET /api/v1/operators/dedup_exact/1.0.0': DEDUP_EXACT,
    'POST /api/v1/recipes/validate': validation,
    'PUT /api/v1/recipe-drafts/draft-1': (body: unknown) => ({ json: { ...DRAFT, ...(body as object) } }),
  });

  it('"Add" puts the operator, at its allowed version, into the draft, with its settings form', async () => {
    mockFetch(routes());
    useDraftsStore.setState({ draft: DRAFT });
    const Step = curateStep();
    render(<Step draft={DRAFT} onAdvance={vi.fn()} />);
    expect(screen.getByTestId('curate-empty')).toBeInTheDocument();
    const add = await screen.findByRole('button', { name: 'Add Sample rows' });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    // the draft holds the step the build will run
    expect(useDraftsStore.getState().draft?.body.steps).toEqual([{ operator: 'sample', version: '1.0.0', params: {} }]);
    // its settings are the operator's own form, not a JSON box
    const count = await screen.findByLabelText(/Rows to keep/);
    fireEvent.change(count, { target: { value: '25000' } });
    expect(useDraftsStore.getState().draft?.body.steps?.[0].params).toEqual({ count: 25000 });
    expect(screen.getByLabelText('Operator for step 1')).toHaveValue('sample');
    expect(screen.getByLabelText('Version for step 1')).toHaveValue('1.0.0');
  });

  it('an operator that is not allowed cannot be added, and says why', async () => {
    const items = CURATE_LIST.items
      .filter((e) => e.name !== 'empty_content') // not installed
      .map((e) => (e.name === 'dedup_exact' ? { ...e, state: 'not_allowed' as const } : e)); // installed, not allowed
    mockFetch({ ...routes(), 'GET /api/v1/operators': { ...CURATE_LIST, items } });
    useDraftsStore.setState({ draft: DRAFT });
    const Step = curateStep();
    render(<Step draft={DRAFT} onAdvance={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add Sample rows' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Add Remove exact duplicates' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add Drop empty rows' })).toBeDisabled();
    expect(screen.getAllByText('not allowed: allow it on Operators')).toHaveLength(1);
    expect(screen.getAllByText('not installed on this deployment')).toHaveLength(1);
  });

  it('Continue to labeling waits for a built version and says so', async () => {
    mockFetch(routes());
    const Step = curateStep();
    const { rerender } = render(<Step draft={DRAFT} onAdvance={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Continue to labeling' })).toBeDisabled();
    expect(screen.getByTestId('curate-continue-why')).toHaveTextContent('Build the version first');
    const built = { ...DRAFT, flow_state: { step: 'curate', choices: { version_id: 'v-9' } } };
    rerender(<Step draft={built} onAdvance={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Continue to labeling' })).toBeEnabled();
  });

  it('shows the registry\'s verdict on the steps', async () => {
    const withStep = { ...DRAFT, body: { format: 'dw.recipe/v1' as const, steps: [{ operator: 'sample', version: '1.0.0', params: { mode: 'count' } }] } };
    const invalid = { valid: false, body_errors: [], steps: [{ index: 1, operator: 'sample', version: '1.0.0', errors: [{ code: 'params_invalid', message: '/count: sampling by count needs a number of rows.' }] }] };
    mockFetch(routes(invalid));
    useDraftsStore.setState({ draft: withStep });
    await act(() => useDraftsStore.getState().validateDraft());
    const Step = curateStep();
    render(<Step draft={withStep} onAdvance={vi.fn()} />);
    expect(screen.getByTestId('curate-check')).toHaveTextContent('Some steps have problems');
    expect(screen.getByTestId('step-errors')).toHaveTextContent('sampling by count needs a number of rows');
  });
});

describe('finding 2: "Build a version from this source" continues where the journey can go on', () => {
  const base: RecipeDraft = { ...DRAFT, dataset_id: null, flow_state: { step: 'curate', choices: {} } };
  it('without a dataset it goes to Goal, with one to Curate, and suggests a dataset name', () => {
    expect(draftPatchFor(base, DETAIL, { continueTo: true }).flow_state.step).toBe('goal');
    expect(draftPatchFor({ ...base, dataset_id: 'd-1' }, DETAIL, { continueTo: true }).flow_state.step).toBe('curate');
    expect(draftPatchFor(base, DETAIL).flow_state.step).toBe('curate'); // the Import step keeps its own step
    expect(suggestedDatasetName('CreativeLang/ColBERT_Humor_Detection')).toBe('colbert-humor-detection');
    expect(suggestedDatasetName('--Weird  Name!!--')).toBe('weird-name');
  });
});

describe('finding 3: IDs are shown, and a recipe build chooses its input by name', () => {
  it('the source drawer shows the source ID with a copy button', () => {
    useSourcesStore.setState({ selected: DETAIL });
    render(<SourceDetailDrawer />);
    const id = screen.getByTestId('source-id');
    expect(id).toHaveTextContent(DETAIL.id);
    expect(within(id).getByRole('button', { name: new RegExp(`Copy source ${DETAIL.id}`) })).toBeInTheDocument();
  });

  it('Build version on a recipe card offers sources and versions by name and sends the chosen ID', async () => {
    const recipe = { id: 'rec-1', name: 'novice-humor-recipe', description: null, archived: false, head_revision_id: 'rev-1', head_hash: 'b'.repeat(64), step_count: 1, providers: ['native'], revision_count: 1, versions_built: 0, created_by: 'Ada', created_at: '', updated_at: '' };
    const { calls } = mockFetch({
      'GET /api/v1/recipes': { items: [recipe], total: 1 },
      'GET /api/v1/datasets': { items: [{ id: 'd-1', name: 'novice-humor' }], total: 1 },
      'GET /api/v1/sources': { items: [SOURCE], total: 1, page: 1, limit: 50 },
      'GET /api/v1/versions': { items: [{ ...VERSION, warnings_count: 0 }], total: 1 },
      'POST /api/v1/recipes/rec-1/build': () => ({ status: 202, json: { job_id: 'job_3', existing_job: false, seed: 1, request_digest: 'x' } }),
    });
    render(<RecipesPanel panel={{ id: 'recipes', label: 'Recipes', title: 'Recipes', subtitle: 's', icon: () => null, feature: '002' } as never} />);
    fireEvent.click(await screen.findByRole('button', { name: /Build version/ }));
    expect(screen.queryByLabelText('Input ID')).toBeNull();
    expect(screen.getByTestId('build-blocked')).toHaveTextContent('Choose the dataset');
    await waitFor(() => expect(within(screen.getByLabelText('Dataset')).getByRole('option', { name: 'novice-humor' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Dataset'), { target: { value: 'd-1' } });
    const picker = screen.getByLabelText('Build from');
    await waitFor(() => expect(within(picker).getByRole('option', { name: /CreativeLang\/ColBERT_Humor_Detection/ })).toBeInTheDocument());
    expect(within(picker).getByRole('option', { name: 'humor-jev9b v2 · 10,914 rows' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: `source:${SOURCE.id}` } });
    expect(screen.getByTestId('input-picker-id')).toHaveTextContent(SOURCE.id);
    const form = screen.getByRole('heading', { name: 'Build a version from novice-humor-recipe' }).parentElement!;
    fireEvent.click(within(form).getByRole('button', { name: 'Build version' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/recipes/rec-1/build')).toBe(true));
    expect(calls.find((c) => c.path === '/api/v1/recipes/rec-1/build')?.body).toMatchObject({ dataset_id: 'd-1', inputs: [{ kind: 'source', source_id: SOURCE.id }] });
  });
});

describe('finding 4 and 9: the Label step caps rows and says why it cannot start', () => {
  const routes = (plan: unknown = PLAN) => ({
    'GET /api/v1/decision-templates': [TEMPLATE],
    'GET /api/v1/rubrics': [],
    'GET /api/v1/versions': { items: [{ ...VERSION, id: 'v1', dataset_name: 'humor', number: 1, total_rows: 200000, warnings_count: 0 }], total: 1 },
    'GET /api/v1/labeling/probes': PROBE_LIST,
    'GET /api/v1/label-runs/plan': plan,
  });

  it('parses the cap strictly', () => {
    expect(parseRowCap('')).toBeNull();
    expect(parseRowCap('25,000')).toBe(25000);
    expect(parseRowCap('0')).toBe('invalid');
    expect(parseRowCap('2.5')).toBe('invalid');
  });

  it('sends row_filter.max_rows in the plan and names the capped count', async () => {
    const { calls } = mockFetch(routes({ ...PROBE_PLAN, rows_total: 25000, rows_to_score: 25000 }));
    render(<LabelStep versionId="v1" />);
    await waitFor(() => expect(screen.getByLabelText('Version to label')).toHaveValue('v1'));
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /high-stakes vs low-stakes/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    fireEvent.change(screen.getByLabelText('Rows to label'), { target: { value: '25000' } });
    expect(screen.getByTestId('row-cap-hint')).toHaveTextContent("at most 25,000 rows of the version's 200,000");
    await waitFor(() => expect(screen.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled(), WAIT);
    const plans = calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan'));
    const sent = JSON.parse(new URLSearchParams(plans.at(-1)!.path.split('?')[1]).get('request')!);
    expect(sent.row_filter).toEqual({ max_rows: 25000 });
  });

  it('an invalid cap blocks the start and says what to type', async () => {
    mockFetch(routes());
    render(<LabelStep versionId="v1" />);
    fireEvent.change(screen.getByLabelText('Rows to label'), { target: { value: '0' } });
    expect(screen.getByTestId('row-cap-hint')).toHaveTextContent('Type a whole number of rows above 0');
    expect(screen.getByTestId('start-blockers')).toHaveTextContent('Fix "Rows to label".');
  });

  it('a refused plan (model not loaded) names the most it can label and states the block and the action', async () => {
    const message = 'meta-llama/Llama-3.1-8B-Instruct is not loaded in miLLM (nothing is). Load meta-llama/Llama-3.1-8B-Instruct in miLLM, then start the run.';
    mockFetch({ ...routes(), 'GET /api/v1/label-runs/plan': () => ({ status: 409, json: { error: { code: 'MODEL_NOT_LOADED', message, details: {} } } }) });
    render(<LabelStep versionId="v1" />);
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /high-stakes vs low-stakes/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    await waitFor(() => expect(within(screen.getByTestId('start-blockers')).getByText(message)).toBeInTheDocument(), WAIT);
    // Re-walk finding 7: the blocked button names the count it would label (the version's rows).
    const button = screen.getByRole('button', { name: 'Label 200,000 rows' });
    expect(button).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Label 0 rows' })).toBeNull();
  });
});

describe('finding 5: the Label step lists the newest versions first and preselects the journey\'s', () => {
  it('asks the API for order=recent, preselects the built version and filters by name', async () => {
    const items = Array.from({ length: 12 }, (_, i) => ({ ...VERSION, id: `old-${i}`, dataset_name: `e2e-test-${i}`, number: 1, warnings_count: 0 }));
    const mine = { ...VERSION, id: 'v-new', dataset_name: 'novice-humor', number: 1, total_rows: 25000, warnings_count: 0 };
    const deleted = { ...VERSION, id: 'v-del', dataset_name: 'gone', state: 'deleted', warnings_count: 0 };
    const { calls } = mockFetch({ 'GET /api/v1/decision-templates': [], 'GET /api/v1/rubrics': [], 'GET /api/v1/labeling/probes': PROBE_LIST, 'GET /api/v1/versions': { items: [mine, ...items, deleted], total: 14 } });
    render(<LabelStep versionId="v-new" />);
    await waitFor(() => expect(screen.getByRole('option', { name: /novice-humor v1 \(25,000 rows\) · this dataset/ })).toBeInTheDocument());
    expect(screen.getByLabelText('Version to label')).toHaveValue('v-new');
    expect(calls.find((c) => c.path.startsWith('/api/v1/versions'))?.path).toContain('order=recent');
    expect(screen.queryByRole('option', { name: /gone/ })).toBeNull(); // deleted versions are not offered
    fireEvent.change(screen.getByLabelText('Find a version'), { target: { value: 'test-1' } });
    const options = within(screen.getByLabelText('Version to label')).getAllByRole('option').map((o) => o.textContent);
    expect(options.some((o) => o?.startsWith('e2e-test-1 '))).toBe(true);
    expect(options.some((o) => o?.startsWith('e2e-test-5 '))).toBe(false);
    expect(options.some((o) => o?.startsWith('novice-humor'))).toBe(true); // the chosen one stays
  });
});

describe('finding 7: the recipe editor chooses operators from the registry and Check answers', () => {
  it('choosing an operator fills its version and form; Check says the recipe is valid', async () => {
    mockFetch({ 'GET /api/v1/operators': CURATE_LIST, 'GET /api/v1/operators/sample/1.0.0': SAMPLE_OP });
    const onValidate = vi.fn();
    const { rerender } = render(<RecipeEditor recipe={null} validation={null} onValidate={onValidate} onSave={vi.fn()} onCancel={vi.fn()} />);
    const op = screen.getByLabelText('Operator for step 1');
    await waitFor(() => expect(within(op).getByRole('option', { name: 'sample' })).toBeInTheDocument());
    expect(within(op).getByRole('option', { name: 'tagger (not allowed)' })).toBeDisabled();
    fireEvent.change(op, { target: { value: 'sample' } });
    expect(screen.getByLabelText('Version for step 1')).toHaveValue('1.0.0');
    await screen.findByLabelText(/Rows to keep/);
    fireEvent.change(screen.getByLabelText(/Rows to keep/), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check recipe' }));
    expect(onValidate).toHaveBeenCalledWith({ format: 'dw.recipe/v1', steps: [{ operator: 'sample', version: '1.0.0', params: { count: 100 } }] });
    expect(screen.getByTestId('check-result')).toHaveTextContent('Checking the recipe');
    rerender(<RecipeEditor recipe={null} validation={VALID(1)} onValidate={onValidate} onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId('check-result')).toHaveTextContent('The recipe is valid: 1 step checked');
    fireEvent.change(screen.getByLabelText(/Rows to keep/), { target: { value: '200' } });
    expect(screen.getByTestId('check-result')).toHaveTextContent('Changed since the last check');
  });
});

describe('finding 8: the probe plan has a plain-language summary first', () => {
  it('says what it detects, how a row is decided, and the check against miStudio', () => {
    const lines = probeSummaryLines(PROBE_PLAN.probe!, PROBE_LIST.items[0], REPRODUCTION_WILL_RUN);
    expect(lines[0]).toBe('This probe detects high-stakes vs low-stakes. It was fitted on Llama-3.1-8B-Instruct and reads layer 16.');
    expect(lines[1]).toBe('Each row gets a probe score; a row scoring 11.91 or more fires (counts as detected). The score is not a probability.');
    expect(lines[2]).toContain('an AUROC inside 0.874 to 0.907');
    const passed = probeSummaryLines(PROBE_PLAN.probe!, PROBE_LIST.items[1], { ...REPRODUCTION_WILL_RUN, state: 'passed', millm_auroc: 0.748, mistudio_auroc: 0.749 });
    expect(passed[0]).toContain('What this probe detects is not stated');
    expect(passed[2]).toBe("It was checked against miStudio and matches: AUROC 0.748 on miLLM against miStudio's 0.749 (accepted range 0.874 to 0.907).");
  });
});

describe('finding 10: old failures fold away and the drawer gets out of the way', () => {
  const now = Date.parse('2026-10-19T12:00:00Z');
  const failed = (id: string, hoursAgo: number): Job => job({ id, kind: 'label_run', status: 'failed', error: 'x', completed_at: new Date(now - hoursAgo * 3600e3).toISOString(), created_at: new Date(now - hoursAgo * 3600e3).toISOString() }) as unknown as Job;

  it('splits newest-first into the last day and older', () => {
    const { recent, older } = splitFailures([failed('a', 30), failed('b', 1), failed('c', 2), failed('d', 72)], now);
    expect(recent.map((j) => j.id)).toEqual(['b', 'c']);
    expect(older.map((j) => j.id)).toEqual(['a', 'd']);
  });

  it('older failures are folded and dismissed together', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/jobs/old-1/dismiss': {}, 'POST /api/v1/jobs/old-2/dismiss': {}, 'GET /api/v1/jobs': { jobs: [] } });
    useJobsStore.setState({ failed: [failed('new-1', 1), failed('old-1', 40), failed('old-2', 50)].map((j) => ({ ...j, completed_at: new Date(Date.now() - (j.id === 'new-1' ? 1 : 40) * 3600e3).toISOString() })) });
    render(<FailedOperations />);
    expect(screen.getByTestId('failed-job-new-1')).toBeVisible();
    const older = screen.getByTestId('older-failures');
    expect(older).not.toHaveAttribute('open');
    fireEvent.click(within(older).getByRole('button', { name: 'Dismiss all 2 older failures' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/dismiss')).map((c) => c.path)).toEqual(['/api/v1/jobs/old-1/dismiss', '/api/v1/jobs/old-2/dismiss']));
  });

  it('moving to another screen, or Escape, closes the drawer', async () => {
    mockFetch({ 'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] }, 'GET /api/v1/approvals': { approvals: [] } });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /Show operations/ }));
    expect(screen.getByRole('dialog', { name: 'Operations' })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('nav-recipes'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Operations' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /Show operations/ }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Operations' })).toBeNull();
  });
});

describe('finding 11 and 12: one size figure, distinct Import buttons', () => {
  it('the import help states the confirmation size in force, as Settings shows it', () => {
    useSourcesStore.setState({ meta: SOURCES_META });
    render(<ImportForm />);
    expect(screen.getByText(new RegExp(`confirmation size, ${formatBytes(SOURCES_META.limits.import_confirm_bytes).replace('.', '\\.')}`))).toBeInTheDocument();
    expect(screen.queryByText(/50 GB by default/)).toBeNull();
  });

  it('the preview modal\'s import button is named apart from the form\'s', async () => {
    const { PREVIEW } = await import('./fixtures001');
    mockFetch({ 'POST /api/v1/sources/hf/preview': PREVIEW });
    render(<ImportForm />);
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: PREVIEW.repo_id } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByRole('button', { name: 'Import the previewed dataset' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Import' })).toHaveLength(1);
  });
});

describe('finding 13: finished runs show their working time and rate', () => {
  it('the card and its numbers', () => {
    render(<LabelRunCard run={run({ state: 'completed', rows_done: 25000, rows_total: 25000, row_filter: { max_rows: 25000 }, timing: { active_seconds: 1250, jobs_timed: 1, rows_scored: 25000, rows_per_second: 20 } })} onOpen={vi.fn()} selected={false} />);
    const card = screen.getByTestId('label-run-card');
    expect(card).toHaveTextContent('took 20m 50s');
    expect(card).toHaveTextContent('20.0 rows/s');
    expect(card).toHaveTextContent('capped at 25,000');
  });
});

describe('finding 14: help links reach a topic that exists', () => {
  it('every step of the guided flow and every linked screen has a topic', () => {
    for (const step of META.guided_steps) expect(HELP_TOPIC_IDS).toContain(step);
    for (const topic of ['journey', 'operators', 'recipes', 'label', 'operations']) expect(HELP_TOPIC_IDS).toContain(topic);
  });

  it('a Help link opens the Help screen at its topic', async () => {
    mockFetch({ 'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] }, 'GET /api/v1/approvals': { approvals: [] } });
    render(<App />);
    render(<HelpLink topic="curate" label="Help with curate" />);
    fireEvent.click(screen.getByRole('button', { name: 'Help with curate' }));
    await waitFor(() => expect(screen.getByTestId('screen-help')).toBeInTheDocument());
    expect(document.getElementById('help-curate')).toHaveAttribute('data-current', 'true');
    expect(document.getElementById('help-curate')).toHaveTextContent('Sample rows');
  });
});

describe('versions store keeps the journey version reachable', () => {
  it('fetchVersion stores a version by id for Profile, Label and Export', async () => {
    mockFetch({ 'GET /api/v1/versions/v-2': VERSION });
    await useVersionsStore.getState().fetchVersion('v-2');
    expect(useVersionsStore.getState().versions['v-2']?.number).toBe(2);
  });
});

// Feature 007's screen parts (FTASKS 12.1 – 12.9, 12.11): the real stores and API client against a
// fetch stub, refusal states, "not measured", the falling warning, cyan for miLLM state.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { discoveredSlots } from '@/components/versions/versionSlots';
import { useGenerationStore } from '@/stores/generationStore';
import { mockFetch } from '@/test/fetchMock';
import type { DiversityReport, GenerationRun } from '@/types/generation';
import type { Version } from '@/types/versions';

import { AxisDiffLine, COMPARE_DEBOUNCE_MS } from './AxisDiffLine';
import { DiscardList } from './DiscardList';
import { DiversityPanel, FALLING_ADVICE } from './DiversityPanel';
import { GenerationRunCard } from './GenerationRunCard';
import { GenerationRunDetail } from './GenerationRunDetail';
import { HeldOutStatus } from './HeldOutStatus';
import { IndependenceCheckRow } from './IndependenceCheckRow';
import { NewStandardRunForm } from './NewStandardRunForm';
import { PairBrowser } from './PairBrowser';
import { TryOnSamplePanel } from './TryOnSamplePanel';

const RUN: GenerationRun = {
  id: 'gr_1', mode: 'standard', target_type: 'sft', state: 'completed', input_version_id: 'v1234567890',
  held_out_splits: ['test'], prompt_column: 'prompt', seed_splits: ['train'], sample_size: 1000, seed: 3,
  n_responses: 2, stages: ['seed', 'respond'], generation_endpoint: { model_id: 'Qwen2.5-7B-Instruct' },
  server_kind: 'millm', generator_identities: [{ model_id: 'Qwen2.5-7B-Instruct', revision: 'abc123def456', set_hash: 'none' }],
  judge_identity: { model_id: 'JEV-9B', revision: 'not reported', set_hash: 'none' }, chosen_side: null,
  engine_path: 'native', pinned: false, revision_reported: true, model_revision: 'abc', failure_reason: null,
  error: null, warnings: [], counts: { 'respond:generated': 1800, 'respond:discarded': 200, 'reason:steering_unreported': 200, pairs: 0 },
  snapshots: [{ side: 'generator', kind: 'none', profile_name: null, profile_updated_at: null, intensity: null, model_id: null, sae_id: null, layer: null, features: [], sent_features: [], set_hash: null }],
  started_by: 'Ada', started_by_origin: 'operator', created_at: '', completed_at: '', job_ids: ['job_1'],
  current_job_id: null, room: 'dataworks/generation-runs/gr_1', resumable: false,
};

const REPORT = (verdict: DiversityReport['verdict'], spread: 'holds' | 'not_measured' = 'holds'): DiversityReport => ({
  id: 'dr_1', version_id: 'v1', reference_version_id: 'v0', column: 'completion', splits: ['train'], sample_size: 5000,
  embedding_identity: spread === 'not_measured' ? null : { served_model: 'embed-small' }, clustering: { basis: 'lexical', k: 50 },
  figures: {
    distinct_2: { version: { value: 0.41, lo: 0.4, hi: 0.42 }, reference: { value: 0.64, lo: 0.63, hi: 0.65 }, verdict: verdict === 'falls' ? 'falls' : 'holds', reason: null },
    embedding_spread: spread === 'not_measured' ? { version: null, reference: null, verdict: 'not_measured', reason: 'no embeddings' } : { version: { value: 0.3, lo: 0.29, hi: 0.31 }, reference: { value: 0.3, lo: 0.29, hi: 0.31 }, verdict: 'holds', reason: null },
  },
  checks: [{ check_id: 'diversity_negative_control', result: 'pass', reason: null }, { check_id: 'diversity_positive_control', result: 'pass', reason: null }],
  verdict, reason: null, created_at: '',
});

beforeEach(() =>
  useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, templates: [], plan: null, planError: null, preview: null, compareResult: null, independence: null, diversity: {}, selectedId: null, error: null }),
);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('GenerationRunCard', () => {
  it('shows the model in cyan, counts with their scale, and the unpinned badge with text', () => {
    render(<GenerationRunCard run={RUN} selected={false} onOpen={() => undefined} />);
    expect(screen.getByText('Qwen2.5-7B-Instruct')).toHaveClass('text-cyan-600');
    expect(screen.getByText('1,800 generated · 200 discarded · 0 pairs')).toBeInTheDocument();
    expect(screen.getByText('Unpinned')).toBeInTheDocument();
    expect(screen.getByRole('progressbar').firstElementChild).toHaveClass('bg-green-500');
  });

  it('uses the indigo fill only while running', () => {
    render(<GenerationRunCard run={{ ...RUN, state: 'running' }} live={{ units_done: 5, units_total: 10 }} selected onOpen={() => undefined} />);
    expect(screen.getByRole('progressbar').firstElementChild).toHaveClass('bg-indigo-500');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  });
});

describe('refusal states', () => {
  it('names the split operator when there is no held-out split', () => {
    render(<HeldOutStatus plan={null} refusal={{ code: 'HELD_OUT_MISSING', message: 'x', details: {} }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Add a split step with a held-out split');
  });

  it('says what to do when the judge is the generator', () => {
    render(
      <IndependenceCheckRow
        result={{ independent: false, judge_identity: RUN.generator_identities[0], generator_identities: RUN.generator_identities, conflicts: [{}], inherited_from: 'judge', message: 'The judge is the generator. Set a generation model in Settings, or steer the generator.' }}
        onCheck={() => undefined}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Not independent: The judge is the generator. Set a generation model in Settings');
    expect(screen.getByRole('button', { name: 'Check judge independence' })).toBeInTheDocument();
  });

  it('a refused plan keeps the start button disabled and shows the message', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/versions': { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'humor', target_type: 'sft', number: 2, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 10, total_bytes: 1, warnings_count: 0 }], total: 1 },
      // The real list is ordered by name, so 009's minimal-pair-v1 comes FIRST; the default must
      // still be respond-v1 (a minimal edit is not an answer).
      'GET /api/v1/generation-templates': { items: [{ id: 'gt_mp', name: 'minimal-pair-v1', version: 1, ref: 'minimal-pair-v1@1', kind: 'respond', description: null, body: {}, content_hash: 'g', builtin: true, used: false, placeholders: ['prompt'] }, { id: 'gt_r', name: 'respond-v1', version: 1, ref: 'respond-v1@1', kind: 'respond', description: null, body: {}, content_hash: 'h', builtin: true, used: false, placeholders: ['prompt'] }], total: 2, page: 1, limit: 50 },
      'POST /api/v1/generation-runs/plan': () => ({ status: 422, json: { error: { code: 'JUDGE_IS_GENERATOR', message: 'The judge is the generator (Qwen, unsteered).', details: {} } } }),
    });
    render(<NewStandardRunForm onStarted={() => undefined} />);
    await waitFor(() => expect(screen.getByRole('option', { name: 'humor v2 (sft)' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Input version'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check the plan' }));
    await waitFor(() => expect(screen.getByTestId('plan-refusal')).toHaveTextContent('The judge is the generator'));
    expect(screen.getByRole('button', { name: /^Generate .* responses$/ })).toBeDisabled();
    const plan = calls.find((c) => c.path.endsWith('/plan'));
    expect(plan?.body).toMatchObject({ mode: 'standard', input_version_id: 'v1', respond_template_id: 'gt_r', target_type: 'sft', generator_setting: { kind: 'none' } });
  });
});

describe('AxisDiffLine', () => {
  it('asks once, 300 ms after the last change, and shows the one differing index', async () => {
    vi.useFakeTimers();
    const { calls } = mockFetch({
      'POST /api/v1/steering-settings/compare': { one_axis: true, differing: [{ index: 4127, a: 0, b: 6 }], differing_index: 4127, not_comparable: [], message: 'Differs on feature 4127 only: 0 vs 6.', code: null },
    });
    render(<AxisDiffLine a={{ kind: 'none' }} b={{ kind: 'profile', profile_name: 'humor' }} />);
    await act(async () => {
      vi.advanceTimersByTime(COMPARE_DEBOUNCE_MS + 10);
    });
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByTestId('axis-diff')).toHaveTextContent('Differs on feature 4127 only: 0 vs 6.'));
    expect(calls.filter((c) => c.path.endsWith('/compare'))).toHaveLength(1);
  });

  it('shows a refusal list as an alert', async () => {
    mockFetch({ 'POST /api/v1/steering-settings/compare': { one_axis: false, differing: [{ index: 1, a: 1, b: 2 }, { index: 2, a: 1, b: 2 }], differing_index: null, not_comparable: [], message: 'The two settings differ on 2 features (1, 2).', code: 'NOT_ONE_AXIS' } });
    render(<AxisDiffLine a={{ kind: 'none' }} b={{ kind: 'none' }} />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('differ on 2 features'));
  });
});

describe('TryOnSamplePanel', () => {
  it('shows the reported steering in cyan and the check', async () => {
    mockFetch({ 'POST /api/v1/generation-runs/preview': { items: [{ prompt: 'Tell a joke', text: 'Why…', model: 'm', reported_steering: 'none', steering_check: 'match', check_reasons: [], error: null }], server_kind: 'millm', model_id: 'm', stopped_early: false } });
    render(<TryOnSamplePanel setting={{ kind: 'none' }} templateId={null} />);
    fireEvent.change(screen.getByLabelText('Prompts to try (one per line, up to 5)'), { target: { value: 'Tell a joke' } });
    fireEvent.click(screen.getByRole('button', { name: 'Try 1 prompt' }));
    await waitFor(() => expect(screen.getByText('none')).toHaveClass('text-cyan-600'));
    expect(screen.getByText(/check: match/)).toBeInTheDocument();
  });
});

describe('DiscardList and PairBrowser', () => {
  it('a missing header reads "not reported", never "unsteered"', () => {
    render(<DiscardList records={[{ stage: 'respond', record_index: 3, side: null, model_id: 'm', requested_set_hash: null, reported_steering: null, steering_check: 'unreported', check_reasons: ['missing'], seed_sent: 1, seed_confirmed: null, outcome: 'discarded', reason_code: 'steering_unreported', prompt: 'p', text: 't' }]} />);
    expect(screen.getByText('not reported')).toHaveClass('text-cyan-600');
    expect(screen.getByText('Steering not reported — missing')).toBeInTheDocument();
  });

  it('a pair shows both reported steerings and which side was chosen', () => {
    render(<PairBrowser pairs={[{ prompt_row_key: 'k', pair_index: 0, chosen_side: 'b', shared_seed: 9, prompt: 'p', text_a: 'plain', text_b: 'funny', steering_a: 'none', steering_b: 'profile;name="humor"' }]} />);
    expect(screen.getByText(/Side B \(chosen\)/)).toBeInTheDocument();
    expect(screen.getByText('profile;name="humor"')).toHaveClass('text-cyan-600');
  });
});

describe('GenerationRunDetail', () => {
  it('shows generator and judge identities side by side and builds the candidate', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/generation-runs/gr_1/records': { items: [], total: 0, page: 1, limit: 50 },
      'POST /api/v1/generation-runs/gr_1/candidate-build': () => ({ status: 202, json: { job_id: 'job_9' } }),
    });
    render(<GenerationRunDetail run={RUN} />);
    expect(screen.getByTestId('identities')).toHaveTextContent('Qwen2.5-7B-Instruct');
    expect(screen.getByTestId('identities')).toHaveTextContent('JEV-9B');
    fireEvent.click(screen.getByRole('button', { name: 'Build candidate version' }));
    await waitFor(() => expect(screen.getByText(/job job_9/)).toBeInTheDocument());
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/candidate-build'))).toBe(true);
  });
});

describe('DiversityPanel and its slot', () => {
  it('a falling verdict warns with the next step and refuses nothing', async () => {
    mockFetch({ 'GET /api/v1/versions/v1/diversity': REPORT('falls') });
    render(<DiversityPanel versionId="v1" />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(FALLING_ADVICE));
    expect(screen.getByRole('alert')).toHaveTextContent('Publishing is not refused');
    expect(screen.getByText('0.41 [0.40, 0.42]')).toBeInTheDocument();
    expect(screen.getByText(/on up to 5,000 rows per side/)).toBeInTheDocument();
  });

  it('says "not measured" without embeddings', async () => {
    mockFetch({ 'GET /api/v1/versions/v1/diversity': REPORT('not_measured', 'not_measured') });
    render(<DiversityPanel versionId="v1" />);
    await waitFor(() => expect(screen.getByText('Not measured')).toBeInTheDocument());
    expect(screen.getByText(/embeddings not measured \(lexical only\)/)).toBeInTheDocument();
  });

  it('offers a report when there is none', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/versions/v1/diversity': () => ({ status: 404, json: { error: { code: 'DIVERSITY_REPORT_NOT_FOUND', message: 'none', details: {} } } }),
      'POST /api/v1/versions/v1/diversity': () => ({ status: 202, json: { job_id: 'job_d', column: 'completion' } }),
    });
    render(<DiversityPanel versionId="v1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Measure diversity' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
  });

  it('the slot is discovered and applies only to versions that bind a generation run', () => {
    const slot = discoveredSlots().find((s) => s.id === 'diversity');
    expect(slot).toBeDefined();
    const version = { bindings: [{ kind: 'generation_run', id: 'gr_1' }] } as unknown as Version;
    expect(slot?.applies(version)).toBe(true);
    expect(slot?.applies({ bindings: [] } as unknown as Version)).toBe(false);
  });

  it('the Assemble step links to the Generation screen', () => {
    const panel = discoveredStepPanels().find((p) => p.step === 'assemble' && p.order === 900);
    expect(panel).toBeDefined();
  });
});

describe('generationStore', () => {
  it('a 404 diversity read is "no report", not an error', async () => {
    mockFetch({ 'GET /api/v1/versions/v9/diversity': () => ({ status: 404, json: { error: { code: 'DIVERSITY_REPORT_NOT_FOUND', message: 'none', details: {} } } }) });
    await useGenerationStore.getState().loadDiversity('v9');
    expect(useGenerationStore.getState().diversity.v9).toBeNull();
    expect(useGenerationStore.getState().error).toBeNull();
  });

  it('progress events update live counts; terminal events re-read the run', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/generation-runs/gr_1': RUN });
    useGenerationStore.getState().applyEvent('gr_1', 'generation_run:progress', { units_done: 3 });
    expect(useGenerationStore.getState().live.gr_1).toEqual({ units_done: 3 });
    useGenerationStore.getState().applyEvent('gr_1', 'generation_run:completed', {});
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/generation-runs/gr_1')).toBe(true));
  });

  it('a start keeps the refusal envelope', async () => {
    mockFetch({ 'POST /api/v1/generation-runs': () => ({ status: 409, json: { error: { code: 'HELD_OUT_MISSING', message: 'No held-out split', details: { next_step: { operator: 'split' } } } } }) });
    const run = await useGenerationStore.getState().start({ mode: 'standard', input_version_id: 'v', prompt_column: 'p', seed_splits: ['train'], sample_size: 1, target_type: 'sft' });
    expect(run).toBeNull();
    expect(useGenerationStore.getState().planError).toEqual({ code: 'HELD_OUT_MISSING', message: 'No held-out split', details: { next_step: { operator: 'split' } } });
  });
});

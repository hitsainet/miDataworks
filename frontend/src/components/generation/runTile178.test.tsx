// 017 FTASKS 17.8 (ENH-006 D94, operator 2026-10-30): what the Generation screen showed for a
// running carrier run — "0 generated · 0 discarded", the generation endpoint's model
// (Qwen2.5-7B-Instruct) instead of the run's generator (Llama-3.1-8B-Instruct) and "Template: not
// recorded" — and what it shows now. The fixture is the production run's shape on 2026-10-31
// (gr_b230cbb9f43a1b0547385c1c): the endpoint and the generator name DIFFERENT models, so a tile
// reading the wrong field fails here. Real components and stores, fetch stubbed.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { getPanel } from '@/config/panels';
import { GENERATION_POLL_MS } from '@/hooks/useGenerationRun';
import { useGenerationStore } from '@/stores/generationStore';
import { mockFetch } from '@/test/fetchMock';
import type { GenerationRun } from '@/types/generation';

import { GenerationRunCard } from './GenerationRunCard';
import { GenerationRunDetail } from './GenerationRunDetail';

const STARTED = '2026-10-30T10:43:54Z';
const NOW = Date.parse('2026-10-31T05:00:00Z');

const CARRIER: GenerationRun = {
  id: 'gr_b230cbb9', mode: 'carrier_slot', target_type: 'detector', state: 'running',
  input_version_id: '67397976-d0b3-4d8b-9bbf-1a6397a2275e', held_out_splits: ['test'], prompt_column: 'text',
  seed_splits: ['train'], sample_size: 21900, seed: 1710, n_responses: 1, stages: ['seed', 'respond'],
  generation_endpoint: { role: 'generation', model_id: 'Qwen2.5-7B-Instruct', base_url: 'http://millm' }, server_kind: 'millm',
  generator_model_id: 'Llama-3.1-8B-Instruct',
  generator_identities: [{ model_id: 'Llama-3.1-8B-Instruct', revision: '0e9e39f249a16976918f6564b8830bc894c89659', set_hash: 'none' }],
  judge_identity: { model_id: 'Qwen2.5-7B-Instruct', revision: 'not reported', set_hash: 'none' },
  chosen_side: null, engine_path: 'native', pinned: true, revision_reported: true, model_revision: '0e9e39f249a1',
  failure_reason: null, error: null, warnings: [], counts: {},
  snapshots: [{ side: 'generator', kind: 'none', profile_name: null, profile_updated_at: null, intensity: null, model_id: 'Llama-3.1-8B-Instruct', sae_id: null, layer: null, features: [], sent_features: [], set_hash: null }],
  started_by: 'agent:dataworks-mcp', started_by_origin: 'agent', created_at: STARTED, completed_at: null,
  job_ids: ['job_334c'], current_job_id: 'job_334c', room: 'dataworks/generation-runs/gr_b230cbb9', resumable: false,
  timing: { queued_at: STARTED, started_at: STARTED, finished_at: null, queued_seconds: 0.5, total_seconds: null, active_seconds: null, attempts: 1 },
  input_version: { id: '67397976-d0b3-4d8b-9bbf-1a6397a2275e', dataset_id: 'd1', dataset_name: 'dlp-ssn-us-pilot', number: 12 },
  expand_template: null, respond_template: null,
  template_map: { column: 'genre', templates: { chat: 'gt_3dbc', email: 'gt_8bdb' } },
  carrier_templates: [
    { value: 'chat', template_id: 'gt_3dbc', template: { id: 'gt_3dbc', name: 'ssn-carrier-chat', version: 4, kind: 'respond', description: null } },
    { value: 'email', template_id: 'gt_8bdb', template: null },
  ],
  progress: { job_id: 'job_334c', percent: 81.3, message: '17800 of 21900 prompts done.' },
  behaviour: { key: 'dlp-ssn-us', meaning: null, source: 'run', source_id: 'gr_b230cbb9', source_name: 'recorded at start' },
  behaviours: [], candidate_versions: [], approval_id: null,
};

const carrier = (over: Partial<GenerationRun> = {}): GenerationRun => ({ ...CARRIER, ...over });

beforeEach(() => useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, selectedId: null, error: null }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('17.8 (a): a running run shows its progress and counts so far', () => {
  it('the tile reads the job progress the worker records when no live event has come', () => {
    render(<GenerationRunCard run={carrier()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-progress')).toHaveTextContent('17800 of 21900 prompts done (81%)');
    expect(screen.getByRole('progressbar', { name: 'Prompts done' })).toHaveAttribute('aria-valuenow', '81');
  });

  it('the tile and the open run show the counts the worker wrote at its last chunk', () => {
    const counts = { 'respond:generated': 15020, 'respond:discarded': 2780, 'reason:slot_missing': 1700, 'reason:slot_repeated': 1080, pairs: 0 };
    const { unmount } = render(<GenerationRunCard run={carrier({ counts })} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('So far: 15,020 generated · 2,780 discarded');
    unmount();
    mockFetch({ 'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={carrier({ counts })} />);
    const outcome = screen.getByTestId('glance-outcome');
    expect(outcome).toHaveTextContent('17800 of 21900 prompts done (81%)');
    expect(outcome).toHaveTextContent('So far: 15,020 generated · 2,780 discarded');
    expect(within(screen.getByTestId('carrier-run')).getByText('15,020')).toBeInTheDocument();
  });

  it('a live progress event is newer than the stored counts while running, never after the end', () => {
    const live = { units_done: 18000, units_total: 21900, counts: { 'respond:generated': 15200, 'respond:discarded': 2800 } };
    const { unmount } = render(<GenerationRunCard run={carrier({ counts: { 'respond:generated': 15020 } })} live={live} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('So far: 15,200 generated · 2,800 discarded');
    expect(screen.getByTestId('run-progress')).toHaveTextContent('18,000 of 21,900 prompts done (82%)');
    unmount();
    const done = carrier({ state: 'completed', completed_at: '2026-10-31T06:00:00Z', progress: null, counts: { 'respond:generated': 18300, 'respond:discarded': 3600, pairs: 0 } });
    render(<GenerationRunCard run={done} live={live} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('18,300 generated · 3,600 discarded');
    expect(screen.getByTestId('run-outcome')).not.toHaveTextContent('So far');
    expect(screen.queryByTestId('run-progress')).toBeNull();
  });

  it('the open run takes the counts of its latest live progress event', () => {
    mockFetch({ 'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 } });
    useGenerationStore.setState({ live: { gr_b230cbb9: { units_done: 18000, units_total: 21900, counts: { 'respond:generated': 15200, 'respond:discarded': 2800 } } } });
    render(<GenerationRunDetail run={carrier({ counts: { 'respond:generated': 15020 } })} />);
    const outcome = screen.getByTestId('glance-outcome');
    expect(outcome).toHaveTextContent('18,000 of 21,900 prompts done (82%)');
    expect(outcome).toHaveTextContent('So far: 15,200 generated · 2,800 discarded');
  });

  it('the Generation page re-reads every unfinished run, so its tile moves without opening it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let reads = 0;
    const { calls } = mockFetch({
      'GET /api/v1/generation-runs': { items: [carrier(), carrier({ id: 'gr_done', state: 'completed', progress: null, current_job_id: null })], total: 2, page: 1, limit: 50 },
      'GET /api/v1/generation-runs/gr_b230cbb9': () => {
        reads += 1;
        return { json: carrier({ counts: { 'respond:generated': 100 * reads }, progress: { job_id: 'job_334c', percent: 90, message: '19710 of 21900 prompts done.' } }) };
      },
    });
    render(<GenerationPanel panel={getPanel('generation')} />);
    await waitFor(() => expect(screen.getAllByTestId('generation-run-card')).toHaveLength(2));
    expect(screen.getAllByTestId('run-outcome')[0]).toHaveTextContent('So far: 0 generated');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS);
    });
    await waitFor(() => expect(screen.getAllByTestId('run-outcome')[0]).toHaveTextContent('So far: 100 generated'));
    expect(screen.getAllByTestId('run-progress')[0]).toHaveTextContent('19710 of 21900 prompts done (90%)');
    const runReads = calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/v1/generation-runs/'));
    expect(runReads.map((c) => c.path)).toEqual(['/api/v1/generation-runs/gr_b230cbb9']);
  });
});

describe('17.8 (b): the run names its own generator', () => {
  it('the tile names the generator, not the endpoint model', () => {
    render(<GenerationRunCard run={carrier()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-generator')).toHaveTextContent(/^Llama-3\.1-8B-Instruct$/);
    expect(screen.getByTestId('generation-run-card')).not.toHaveTextContent('Qwen2.5-7B-Instruct');
  });

  it('the open run names the generator and says the endpoint setting names another model', () => {
    mockFetch({ 'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={carrier()} />);
    const g = screen.getByTestId('glance-generator');
    expect(g).toHaveTextContent('Generator: Llama-3.1-8B-Instruct on the generation endpoint (miLLM, native path)');
    expect(g).toHaveTextContent("this run sends its own generator; the endpoint's setting names Qwen2.5-7B-Instruct");
  });

  it('a run of the older modes still names the endpoint model, with no note', () => {
    mockFetch({ 'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={carrier({ mode: 'standard', generator_model_id: null, carrier_templates: [], template_map: null })} />);
    const g = screen.getByTestId('glance-generator');
    expect(g).toHaveTextContent('Generator: Qwen2.5-7B-Instruct on the generation endpoint');
    expect(g).not.toHaveTextContent('its own generator');
  });
});

describe('17.8 (c): a carrier run shows its template map', () => {
  it('the open run lists each value with its template by name, and a missing one by id', () => {
    mockFetch({ 'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={carrier()} />);
    expect(screen.getByTestId('glance-how')).not.toHaveTextContent('not recorded');
    const t = screen.getByTestId('glance-carrier-templates');
    expect(t).toHaveTextContent('Templates: one per genre');
    expect(t).toHaveTextContent('chat: ssn-carrier-chat v4');
    expect(t).toHaveTextContent('email: template gt_8bdb (no longer present)');
  });

  it('the tile says how many templates and by which column', () => {
    render(<GenerationRunCard run={carrier()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('generation-run-card')).toHaveTextContent('· 2 templates, one per genre ·');
  });
});

// Operator, 2026-10-31: a tile opens its run; the same tile closes it; another tile switches; the X
// and Escape close it.
describe('the open run toggles from its tile', () => {
  const other = carrier({ id: 'gr_other', state: 'completed', progress: null, current_job_id: null, input_version: { ...CARRIER.input_version!, number: 13 } });
  async function panel() {
    mockFetch({
      'GET /api/v1/generation-runs': { items: [carrier(), other], total: 2, page: 1, limit: 50 },
      'GET /api/v1/generation-runs/gr_b230cbb9/records': { items: [], total: 0, page: 1, limit: 50 },
      'GET /api/v1/generation-runs/gr_other/records': { items: [], total: 0, page: 1, limit: 50 },
    });
    useGenerationStore.setState({ byId: { gr_b230cbb9: carrier(), gr_other: other } });
    render(<GenerationPanel panel={getPanel('generation')} />);
    await waitFor(() => expect(screen.getAllByTestId('generation-run-card')).toHaveLength(2));
    return screen.getAllByTestId('generation-run-card');
  }
  const title = () => screen.queryByTestId('run-detail-title')?.textContent ?? null;

  it('opens by click, closes on a second click of the same tile, switches on another', async () => {
    const [first, second] = await panel();
    fireEvent.click(first);
    expect(title()).toContain('v12');
    expect(first).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(first);
    expect(screen.queryByTestId('generation-run-detail')).toBeNull();
    expect(first).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(first);
    fireEvent.click(second);
    expect(title()).toContain('v13');
  });

  it('the X and Escape close it', async () => {
    const [first] = await panel();
    fireEvent.click(first);
    fireEvent.click(within(screen.getByTestId('generation-run-detail')).getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('generation-run-detail')).toBeNull();
    fireEvent.click(first);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('generation-run-detail')).toBeNull();
  });
});

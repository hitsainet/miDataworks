// A generation run read at a glance (operator request 2026-10-21): what it is for (behaviour,
// dataset by name), when (started, ended, running for, duration), who, how and the outcome, on the
// tile and in the open run, with every missing-data fallback said in words. The panel filters by
// status and behaviour and sorts newest first. Real components, real stores, fetch stubbed.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { getPanel } from '@/config/panels';
import { useGenerationStore } from '@/stores/generationStore';
import { useVersionsStore } from '@/stores/versionsStore';
import { mockFetch } from '@/test/fetchMock';
import type { GenerationRun } from '@/types/generation';

import { GenerationRunCard } from './GenerationRunCard';
import { GenerationRunDetail } from './GenerationRunDetail';
import { RunAtAGlance } from './RunAtAGlance';
import { REVISION_TOOLTIP, whenLine } from './runSummary';

const QUEUED_AT = '2026-10-20T07:47:07Z';
const STARTED_AT = '2026-10-20T07:47:09Z';
const FINISHED_AT = '2026-10-20T07:50:02Z';

const BASE: GenerationRun = {
  id: 'gr_68d1084f', mode: 'minimal_pairs', target_type: 'detector', state: 'completed',
  input_version_id: '064dfbfb-964d-4e49-a90a-27e1736e7100', held_out_splits: ['test'], prompt_column: 'completion',
  seed_splits: ['b_outdoors'], sample_size: 300, seed: 1020, n_responses: 1, stages: ['seed', 'respond'],
  generation_endpoint: { role: 'generation', model_id: 'Qwen2.5-7B-Instruct' }, server_kind: 'millm',
  generator_identities: [{ model_id: 'Qwen2.5-7B-Instruct', revision: 'not reported', set_hash: 'none' }],
  judge_identity: { model_id: 'Llama-3.1-8B-Instruct', revision: '0e9e39f249a16976918f6564b8830bc894c89659', set_hash: 'none' },
  chosen_side: null, engine_path: 'native', pinned: true, revision_reported: false, model_revision: null,
  failure_reason: null, error: null, warnings: [],
  counts: { 'respond:generated': 290, 'respond:discarded': 10, 'reason:parse_failure': 7, 'reason:empty_response': 3, pairs: 0 },
  snapshots: [{ side: 'generator', kind: 'none', profile_name: null, profile_updated_at: null, intensity: null, model_id: null, sae_id: null, layer: null, features: [], sent_features: [], set_hash: null }],
  started_by: 'Sean McIntyre', started_by_origin: 'operator', created_at: QUEUED_AT, completed_at: FINISHED_AT,
  job_ids: ['job_1'], current_job_id: null, room: 'r', resumable: false,
  timing: { queued_at: QUEUED_AT, started_at: STARTED_AT, finished_at: FINISHED_AT, queued_seconds: 2, total_seconds: 173, active_seconds: 173, attempts: 1 },
  input_version: { id: '064dfbfb-964d-4e49-a90a-27e1736e7100', dataset_id: 'd1', dataset_name: 'highstakes-chat', number: 3 },
  respond_template: { id: 'gt_1', name: 'minimal-pair-outdoors', version: 2, kind: 'respond', description: 'Flip high stakes to low stakes with one edit.', sampling: { temperature: 0.2, top_p: 1, max_tokens: 512 } },
  expand_template: null,
  behaviour: { key: 'high-stakes', meaning: 'the answer carries real consequences for someone', source: 'detector_set', source_id: 'dts_1', source_name: 'highstakes-chat-pilot' },
  behaviours: [
    { key: 'high-stakes', meaning: 'the answer carries real consequences for someone', source: 'detector_set', source_id: 'dts_1', source_name: 'highstakes-chat-pilot' },
    { key: null, meaning: null, source: 'rubric', source_id: 'rb_1', source_name: 'stakes-judge v1' },
  ],
  minimal_pair_chain: { id: 'mpc_1', state: 'completed', stage: 'done', rubric_id: 'rb_1', rubric_name: 'stakes-judge v1', flip_from: 'high', flip_to: 'low', pair_version: { id: 'e1f7cedc-0000', dataset_id: 'd1', dataset_name: 'highstakes-chat', number: 5 } },
  candidate_versions: [],
  approval_id: null,
};

const run = (over: Partial<GenerationRun> = {}): GenerationRun => ({ ...BASE, ...over });
const NOW = Date.parse('2026-10-20T08:00:00Z');

beforeEach(() => useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, selectedId: null, error: null }));
afterEach(() => vi.unstubAllGlobals());

describe('the run tile', () => {
  it('leads with what it is for: kind, dataset by name and the behaviour', () => {
    render(<GenerationRunCard run={run()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-title')).toHaveTextContent('Minimal pairs from highstakes-chat v3');
    expect(screen.getByTestId('run-title')).toHaveAttribute('title', expect.stringContaining('064dfbfb-964d'));
    expect(screen.getByTestId('run-behaviour')).toHaveTextContent('Behaviour: high-stakes — the answer carries real consequences for someone');
    // The bare hash is no longer the primary text.
    expect(screen.queryByText(/^version 064dfbfb$/)).toBeNull();
  });

  it('says when it started, when it ended and how long it took, in Eastern time', () => {
    render(<GenerationRunCard run={run()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-when')).toHaveTextContent('Took 2m 53s · started Oct 20, 2026, 3:47 AM ET · ended Oct 20, 2026, 3:50 AM ET');
  });

  it('a running run says how long it has been running', () => {
    const running = run({ state: 'running', completed_at: null, timing: { ...BASE.timing!, finished_at: null, total_seconds: null, active_seconds: null } });
    render(<GenerationRunCard run={running} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-when')).toHaveTextContent('Running for 12m 51s · started Oct 20, 2026, 3:47 AM ET');
    expect(screen.getByTestId('run-outcome')).not.toHaveTextContent('candidate');
  });

  it('a queued run says how long it has waited', () => {
    const queued = run({ state: 'queued', completed_at: null, timing: { ...BASE.timing!, started_at: null, finished_at: null, total_seconds: null, queued_seconds: null, active_seconds: null } });
    render(<GenerationRunCard run={queued} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-when')).toHaveTextContent('Queued Oct 20, 2026, 3:47 AM ET, waiting 12m 53s');
  });

  it('a failed run says how long it ran before failing', () => {
    const failed = run({ state: 'failed', timing: { ...BASE.timing!, total_seconds: 65 } });
    render(<GenerationRunCard run={failed} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-when')).toHaveTextContent('Failed after 1m 5s');
  });

  it('the revision note is quiet text with an explanation, not an amber pill', () => {
    render(<GenerationRunCard run={run()} selected={false} onOpen={() => undefined} now={NOW} />);
    const note = screen.getByTestId('revision-not-reported');
    expect(note).toHaveAttribute('title', REVISION_TOOLTIP);
    expect(note.className).not.toMatch(/amber/);
    expect(screen.queryByText('Revision not reported')).toBeNull();
  });

  it('the outcome names the top discard reasons and the version the pairs went into', () => {
    render(<GenerationRunCard run={run()} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('290 generated · 10 discarded (Did not parse 7 · Empty answer 3) · 0 pairs · pairs in highstakes-chat v5');
  });

  it('a standard run names its candidate version, or says there is none yet', () => {
    const std = run({ mode: 'standard', minimal_pair_chain: null, candidate_versions: [{ id: 'c1', dataset_id: 'd1', dataset_name: 'highstakes-chat', number: 4 }] });
    const { unmount } = render(<GenerationRunCard run={std} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('candidate highstakes-chat v4');
    unmount();
    render(<GenerationRunCard run={{ ...std, candidate_versions: [] }} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-outcome')).toHaveTextContent('no candidate version yet');
  });

  it('missing data reads in words: no behaviour, no names, no timing', () => {
    const bare = run({ behaviour: null, behaviours: [], input_version: null, timing: null, respond_template: null, minimal_pair_chain: null, candidate_versions: undefined, completed_at: null });
    render(<GenerationRunCard run={bare} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-title')).toHaveTextContent('Minimal pairs from version 064dfbfb');
    expect(screen.getByTestId('run-behaviour')).toHaveTextContent('No behaviour set');
    expect(screen.getByTestId('run-when')).toHaveTextContent('Completed, duration not recorded · started Oct 20, 2026, 3:47 AM ET · ended time not recorded');
  });

  it('a behaviour found without a key still says what a positive means', () => {
    const b = { key: null, meaning: 'humorous', source: 'detector_set' as const, source_id: 'dts_2', source_name: 'humor-set' };
    render(<GenerationRunCard run={run({ behaviour: b, behaviours: [b] })} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByTestId('run-behaviour')).toHaveTextContent('Behaviour: humorous (no behaviour key set)');
  });

  it('an agent start says so', () => {
    render(<GenerationRunCard run={run({ started_by: 'agent:mistudio-mcp', started_by_origin: 'agent' })} selected={false} onOpen={() => undefined} now={NOW} />);
    expect(screen.getByText(/by an AI agent \(through miStudio's MCP server\)/)).toBeInTheDocument();
  });
});

describe('the open run, at a glance', () => {
  it('the open run shows the at-a-glance block with its title (wiring)', () => {
    mockFetch({ 'GET /api/v1/generation-runs/gr_68d1084f/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={run()} />);
    expect(screen.getByTestId('run-detail-title')).toHaveTextContent('Minimal pairs from highstakes-chat v3');
    const glance = screen.getByTestId('run-at-a-glance');
    expect(within(glance).getByTestId('glance-behaviour')).toHaveTextContent('high-stakes');
    expect(within(glance).getByTestId('glance-when')).toHaveTextContent('Took 2m 53s');
  });

  it('lists every behaviour with its source, and the dataset by name with the id as small print', () => {
    render(<RunAtAGlance run={run()} now={NOW} />);
    const b = screen.getByTestId('glance-behaviour');
    expect(b).toHaveTextContent('high-stakes — positive means: the answer carries real consequences for someone (from detector set highstakes-chat-pilot)');
    expect(b).toHaveTextContent('no behaviour key set (from rubric stakes-judge v1)');
    const d = screen.getByTestId('glance-dataset');
    expect(within(d).getByRole('button', { name: 'highstakes-chat v3' })).toBeInTheDocument();
    expect(d).toHaveTextContent('→ detector training rows');
    expect(d).toHaveTextContent('version id 064dfbfb-964d-4e49-a90a-27e1736e7100');
  });

  it('the key recorded at start leads, and a derived record naming another key is noted', () => {
    const recorded = { key: 'stakes-a', meaning: null, source: 'run' as const, source_id: 'gr_1', source_name: 'recorded at start' };
    const derived = { key: 'stakes-b', meaning: null, source: 'rubric' as const, source_id: 'rb_1', source_name: 'stakes-judge v1' };
    const note = "This run was started for behaviour 'stakes-a'; rubric stakes-judge v1 now says 'stakes-b'. The run's recorded key 'stakes-a' stands.";
    render(<RunAtAGlance run={run({ behaviour: recorded, behaviours: [recorded, derived], behaviour_key: 'stakes-a', behaviour_note: note })} now={NOW} />);
    const b = screen.getByTestId('glance-behaviour');
    expect(b).toHaveTextContent('stakes-a (recorded on the run at start)');
    expect(screen.getByTestId('glance-behaviour-note')).toHaveTextContent(note);
  });

  it('says how to set a behaviour when none is found', () => {
    render(<RunAtAGlance run={run({ behaviour: null, behaviours: [] })} now={NOW} />);
    expect(screen.getByTestId('glance-behaviour')).toHaveTextContent('No behaviour set. Set one by giving a behaviour key to a detector set');
  });

  it('when: start, end, duration, wait and attempts', () => {
    render(<RunAtAGlance run={run({ timing: { ...BASE.timing!, queued_seconds: 95, attempts: 2, active_seconds: 150 } })} now={NOW} />);
    const w = screen.getByTestId('glance-when');
    expect(w).toHaveTextContent('Took 2m 53s · started Oct 20, 2026, 3:47 AM ET · ended Oct 20, 2026, 3:50 AM ET');
    expect(w).toHaveTextContent('waited 1m 35s for a worker · working time 2m 30s · 2 attempts (resumed 1 time)');
  });

  it('how: the mode in words, the chain, template with sampling, generator, judge revision, seeds', () => {
    render(<RunAtAGlance run={run()} now={NOW} />);
    const h = screen.getByTestId('glance-how');
    expect(h).toHaveTextContent('Minimal pairs: each seed row is rewritten with the smallest edit that flips the target behaviour');
    expect(h).toHaveTextContent('judged with stakes-judge v1, flipping the verdict from "high" to "low"');
    expect(h).toHaveTextContent('Template: minimal-pair-outdoors v2 (respond) · temperature 0.2, top-p 1, up to 512 tokens');
    expect(h).toHaveTextContent('Generator: Qwen2.5-7B-Instruct on the generation endpoint (miLLM, native path) · revision not reported');
    expect(h).toHaveTextContent('Judge: Llama-3.1-8B-Instruct @ 0e9e39f249a1');
    expect(h).toHaveTextContent('Steering: Unsteered');
    expect(h).toHaveTextContent('Seeds: 300 seed rows from highstakes-chat v3 (b_outdoors) × 1 response each · column completion · seed 1020');
  });

  it('who: an operator, or an agent with its approval', () => {
    const { unmount } = render(<RunAtAGlance run={run()} now={NOW} />);
    expect(screen.getByTestId('glance-who')).toHaveTextContent('Started by Sean McIntyre (operator)');
    unmount();
    render(<RunAtAGlance run={run({ started_by: 'agent:dataworks-mcp', started_by_origin: 'agent', approval_id: 'apr_9' })} now={NOW} />);
    const who = screen.getByTestId('glance-who');
    expect(who).toHaveTextContent("Started by an AI agent (through miDataworks' MCP server) · approved by the operator");
    fireEvent.click(within(who).getByRole('button', { name: 'see Approvals' }));
    expect(window.location.hash).toBe('#/approvals');
  });

  it('outcome: top reasons, the pair version and the candidate open the version screen', () => {
    useVersionsStore.setState({ selectedId: null });
    render(<RunAtAGlance run={run({ candidate_versions: [{ id: 'cand-1', dataset_id: 'd1', dataset_name: 'highstakes-chat', number: 4 }] })} now={NOW} />);
    const o = screen.getByTestId('glance-outcome');
    expect(o).toHaveTextContent('Top discard reasons: Did not parse 7 · Empty answer 3');
    expect(o).toHaveTextContent('Pairs: highstakes-chat v5');
    fireEvent.click(within(o).getByRole('button', { name: 'highstakes-chat v4' }));
    expect(useVersionsStore.getState().selectedId).toBe('cand-1');
    expect(window.location.hash).toBe('#/version');
  });
});

describe('the run list', () => {
  const pii = run({ id: 'gr_a', created_at: '2026-10-19T10:00:00Z', behaviour: { ...BASE.behaviour!, key: 'pii', meaning: 'personal data' }, state: 'failed' });
  const stakes = run({ id: 'gr_b', created_at: '2026-10-20T10:00:00Z' });
  const none = run({ id: 'gr_c', created_at: '2026-10-18T10:00:00Z', behaviour: null, behaviours: [] });

  function titles() {
    return screen.getAllByTestId('generation-run-card').map((c) => within(c).getByTestId('run-behaviour').textContent);
  }

  it('is newest first and filters by behaviour and status', async () => {
    mockFetch({ 'GET /api/v1/generation-runs': { items: [none, pii, stakes], total: 3, page: 1, limit: 50 } });
    render(<GenerationPanel panel={getPanel('generation')} />);
    await waitFor(() => expect(screen.getAllByTestId('generation-run-card')).toHaveLength(3));
    expect(titles()[0]).toContain('high-stakes');
    expect(titles()[2]).toContain('No behaviour set');
    fireEvent.change(screen.getByTestId('filter-behaviour'), { target: { value: 'pii' } });
    expect(titles()).toEqual([expect.stringContaining('pii')]);
    expect(screen.getByText('1 of 3 runs shown')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('filter-behaviour'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('filter-state'), { target: { value: 'failed' } });
    expect(titles()).toEqual([expect.stringContaining('pii')]);
    fireEvent.change(screen.getByTestId('filter-state'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('sort-runs'), { target: { value: 'oldest' } });
    expect(titles()[0]).toContain('No behaviour set');
  });
});

describe('whenLine', () => {
  it('reads the run row when the backend sent no timing', () => {
    expect(whenLine(run({ timing: undefined, created_at: QUEUED_AT, completed_at: FINISHED_AT }), NOW)).toContain('ended Oct 20, 2026, 3:50 AM ET');
  });
});

import type { Extra } from './fixtures';
import { V2 } from './fixtures002';

// Feature 007's API answers for the Generation screen and the diversity slot (no backend needed).
export const RUN = {
  id: 'gr_1', mode: 'standard', target_type: 'sft', state: 'completed', input_version_id: 'v-sft-0001',
  held_out_splits: ['test'], prompt_column: 'prompt', seed_splits: ['train'], sample_size: 1000, seed: 3, n_responses: 2,
  stages: ['seed', 'respond'], generation_endpoint: { model_id: 'Qwen2.5-7B-Instruct' }, server_kind: 'millm',
  generator_identities: [{ model_id: 'Qwen2.5-7B-Instruct', revision: 'a1b2c3d4e5f6', set_hash: 'none' }],
  judge_identity: { model_id: 'JEV-9B-decision', revision: 'not reported', set_hash: 'none' }, chosen_side: null,
  engine_path: 'native', pinned: true, revision_reported: true, model_revision: 'a1b2c3d4e5f6', failure_reason: null, error: null,
  warnings: [], counts: { 'respond:generated': 1800, 'respond:discarded': 200, 'reason:steering_unreported': 200, pairs: 0 },
  snapshots: [{ side: 'generator', kind: 'none', profile_name: null, profile_updated_at: null, intensity: null, model_id: null, sae_id: null, layer: null, features: [], sent_features: [], set_hash: null }],
  started_by: 'Ada', started_by_origin: 'operator', created_at: '2026-10-07T12:00:00Z', completed_at: '2026-10-07T12:30:00Z',
  job_ids: ['job_1'], current_job_id: null, room: 'dataworks/generation-runs/gr_1', resumable: false,
};

const SFT = { id: 'v-sft-0001', dataset_id: 'd-9', dataset_name: 'helpful-sft', target_type: 'sft', number: 3, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 4000, total_bytes: 1, warnings_count: 0 };
const NOSPLIT = { ...SFT, id: 'v-nosplit', number: 1 };
const MINIMAL = {
  id: 'gt_m', name: 'minimal-pair-v1', version: 1, ref: 'minimal-pair-v1@1', kind: 'respond',
  description: 'Minimal pairs (009): one minimal edit that flips the concept. Clone it and name your concept in the prompt.',
  body: { prompt: 'Edit the text below as little as possible so that the concept it expresses is flipped to its opposite.\n\nText:\n{prompt}', system: 'You make minimal edits to text. You never explain, add commentary or quote the instructions.', sampling: { temperature: 0.2, top_p: 1.0, max_tokens: 512 }, structured_output: 'none' },
  content_hash: 'h2', builtin: true, used: false, placeholders: ['prompt'],
};
const TEMPLATES = { items: [{ id: 'gt_r', name: 'respond-v1', version: 1, ref: 'respond-v1@1', kind: 'respond', description: null, body: {}, content_hash: 'h', builtin: true, used: false, placeholders: ['prompt'] }, MINIMAL], total: 2, page: 1, limit: 50 };
const PLAN = {
  mode: 'standard', target_type: 'sft', held_out: { present: true, splits: ['test'], origin_version_id: 'v-sft-0001' },
  seed_rows_available: 3600, seed_rows_selected: 100, responses_per_prompt: 1, expected_requests: 100, stages: ['seed', 'respond'],
  engine_path: 'native', server_kind: 'millm', resident_model: 'Qwen2.5-7B-Instruct', model_revision: 'a1b2c3d4e5f6', snapshots: [],
  differing_index: null, generator_identities: RUN.generator_identities, judge_identity: RUN.judge_identity, independence: 'independent',
  pinned_expected: true, warnings: [],
};
const REPORT = {
  id: 'dr_1', version_id: 'v-2', reference_version_id: 'v-1', column: 'text', splits: ['train'], sample_size: 5000,
  embedding_identity: null, clustering: { basis: 'lexical', k: 50 },
  figures: {
    distinct_2: { version: { value: 0.41, lo: 0.4, hi: 0.42 }, reference: { value: 0.64, lo: 0.63, hi: 0.65 }, verdict: 'falls', reason: null },
    embedding_spread: { version: null, reference: null, verdict: 'not_measured', reason: 'no embeddings endpoint' },
  },
  checks: [{ check_id: 'diversity_negative_control', result: 'pass', reason: null }, { check_id: 'diversity_positive_control', result: 'pass', reason: null }],
  verdict: 'falls', reason: null, created_at: '2026-10-07T12:00:00Z',
};

export function api007(seen: Array<{ method: string; path: string; body: unknown }> = []): Extra {
  return (path, method, _url, body) => {
    seen.push({ method, path, body });
    if (path === '/api/v1/generation-runs' && method === 'GET') return { items: [RUN], total: 1, page: 1, limit: 50 };
    if (path === '/api/v1/generation-runs' && method === 'POST') return { status: 202, body: { ...RUN, id: 'gr_2', state: 'queued', current_job_id: 'job_2' } };
    if (path === '/api/v1/generation-runs/gr_1/records') return { items: [{ stage: 'respond', record_index: 7, side: null, model_id: 'Qwen2.5-7B-Instruct', requested_set_hash: null, reported_steering: null, steering_check: 'unreported', check_reasons: ['missing'], seed_sent: 1, seed_confirmed: null, outcome: 'discarded', reason_code: 'steering_unreported', prompt: 'p', text: 't' }], total: 1, page: 1, limit: 50 };
    if (path === '/api/v1/generation-templates' && method === 'GET') return TEMPLATES;
    if (path === '/api/v1/generation-templates' && method === 'POST') {
      const b = body as { name: string; kind: string; description: string | null; body: { prompt: string } };
      if (b.name === 'respond-v1') return { status: 409, body: { error: { code: 'TEMPLATE_NAME_EXISTS', message: "A template named 'respond-v1' exists; clone it to make version 2.", details: { name: 'respond-v1', latest_version: 1 } } } };
      const found = [...b.body.prompt.matchAll(/\{([^{}]+)\}/g)].map((m) => m[1]);
      return { status: 201, body: { id: 'gt_new', name: b.name, version: 1, ref: `${b.name}@1`, kind: b.kind, description: b.description, body: b.body, content_hash: 'h3', builtin: false, used: false, placeholders: [...new Set(found)] } };
    }
    if (path === '/api/v1/generation-templates/gt_m/clone') {
      const b = body as { description: string | null; body: { prompt: string } };
      return { status: 201, body: { ...MINIMAL, id: 'gt_m2', version: 2, ref: 'minimal-pair-v1@2', builtin: false, description: b.description, body: b.body, placeholders: ['prompt'] } };
    }
    if (path === '/api/v1/versions' && method === 'GET') return { items: [SFT, NOSPLIT], total: 2 };
    if (path === '/api/v1/generation-runs/plan') {
      const b = body as { input_version_id?: string; mode?: string };
      if (b?.input_version_id === 'v-nosplit') return { status: 409, body: { error: { code: 'HELD_OUT_MISSING', message: 'This version has no held-out split.', details: {} } } };
      if (b?.mode === 'steered_pairs') return { status: 422, body: { error: { code: 'NOT_ONE_AXIS', message: 'The two settings are identical: a steered pair needs one feature to differ.', details: {} } } };
      return PLAN;
    }
    if (path === '/api/v1/steering-settings/compare') return { one_axis: false, differing: [], differing_index: null, not_comparable: [], message: 'The two settings are identical: a steered pair needs one feature to differ.', code: 'NOT_ONE_AXIS' };
    if (path === '/api/v1/versions/v-2') return { ...V2, bindings: [{ kind: 'generation_run', id: 'gr_1' }] };
    if (path === '/api/v1/versions/v-2/diversity') return REPORT;
    return undefined;
  };
}

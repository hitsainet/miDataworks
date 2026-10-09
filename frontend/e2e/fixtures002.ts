// Feature 002's API answers for the Playwright walks (shapes copied from real API output).
import type { Extra } from './fixtures';

export const META = {
  target_types: ['sft', 'dpo', 'kto', 'grpo_prompt', 'prm', 'detector', 'untyped'],
  default_content_columns: { sft: ['messages', 'text'], dpo: ['prompt', 'chosen', 'rejected'], kto: ['prompt', 'completion'], grpo_prompt: ['prompt'], prm: ['prompt', 'completions'], detector: [], untyped: [] },
  event_kinds: ['dropped', 'changed', 'added', 'split_assigned'], version_states: ['completed', 'deleted'], input_kinds: ['source', 'version'],
  column_roles: ['content', 'metadata', 'system'], rowkey_schemes: ['dw.rowkey/v1'], default_rowkey_scheme: 'dw.rowkey/v1',
  binding_kinds: ['label_run', 'generation_run'], guided_steps: ['import', 'goal', 'profile', 'curate', 'label', 'assemble', 'export'],
};

const KEY = '3f2a9c1b7e5d4a8f9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a';
const split = (name: string, rows: number, heldOut: boolean, v: string) => ({ name, held_out: heldOut, rows, bytes: rows * 570, file_sha256: 'd'.repeat(64), logical_digest: 'e'.repeat(64), path: `versions/${v}/${name}.parquet` });
const steps = [
  { step_index: 1, operator: 'threshold_labeler', operator_version: '1', reused: true, rows_in: 25000, rows_out: 11805, dropped: 13195, changed: 0, added: 0, split_assigned: 0, reasons: [{ kind: 'dropped', reason_code: 'inside_band', count: 13195, example: 'P 0.31 inside [0.20, 0.50]' }] },
  { step_index: 2, operator: 'balance', operator_version: '1', reused: false, rows_in: 11805, rows_out: 10914, dropped: 891, changed: 0, added: 0, split_assigned: 0, reasons: [{ kind: 'dropped', reason_code: 'balance', count: 891, example: 'downsampled to 5,457 (seed 20261005)' }] },
  { step_index: 3, operator: 'split', operator_version: '1', reused: false, rows_in: 10914, rows_out: 10914, dropped: 0, changed: 0, added: 0, split_assigned: 10914, reasons: [] },
];
const version = (id: string, number: number, parent: string | null, rows: number) => ({
  id, dataset_id: 'd-1', dataset_name: 'humor-jev9b', target_type: 'detector', number, state: 'completed', is_head: number === 2, superseded_by: number === 2 ? null : 2,
  parent_version_id: parent, total_rows: rows, total_bytes: rows * 570, recipe_hash: 'b'.repeat(64), seed: 20261005, created_by: 'Ada', created_at: '2026-10-06T12:00:00Z',
  request_digest: 'c'.repeat(64), inputs: parent ? [{ kind: 'version', version_id: parent }] : [{ kind: 'source', source_id: 's-1', revision: '2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac' }],
  recipe_revision_id: 'rev-1', recipe_id: 'rec-1', bindings: [], rowkey_scheme: 'dw.rowkey/v1', column_roles: { text: 'content', label: 'metadata' },
  splits: number === 2 ? [split('train', 9824, false, id), split('test', 1090, true, id)] : [split('train', rows, false, id)],
  held_out_origin_version_id: number === 2 ? id : null, warnings: [], drop_summary: number === 2 ? steps : [], manifest_sha256: '2'.repeat(64), build_job_id: 'job_9', created_by_origin: 'operator',
});
export const V1 = version('v-1', 1, null, 25000);
export const V2 = version('v-2', 2, 'v-1', 10914);
const summary = (v: typeof V1) => ({ ...v, warnings_count: 0 });

const DROPPED = {
  row_key: KEY, status: 'dropped', present_in: [], searched: ['v-1', 'v-2'], version_deleted: false, trail: [],
  origin: { source_id: 's-1', source_locator: 'train:6' },
  dropped_at: { version_id: 'v-2', version_number: 2, step_index: 1, operator: 'threshold_labeler', operator_version: '1', kind: 'dropped', from_key: KEY, to_key: null, reason_code: 'inside_band', reason: 'P 0.31 is inside the uncertain band [0.20, 0.50]', statistic_name: 'p', statistic_value: 0.31, statistic_text: null, threshold: { value: [0.2, 0.5], comparator: 'between' } },
};

const COMPARE = {
  version_a: { id: 'v-2', number: 2, total_rows: 10914 }, version_b: { id: 'v-1', number: 1, total_rows: 25000 },
  splits: [{ split: 'train', rows_a: 9824, rows_b: 25000, difference: -15176, label_balance: {} }, { split: 'test', rows_a: 1090, rows_b: 0, difference: 1090, label_balance: {} }],
  keys: { added: 0, removed: 14086, kept: 10914, changed: 0, n_a: 10914, n_b: 25000 },
  drop_log: [{ operator: 'threshold_labeler', reason_code: 'inside_band', a: 13195, b: 0, difference: 13195 }, { operator: 'balance', reason_code: 'balance', a: 891, b: 0, difference: 891 }],
  distributions: [{ column: 'text', kind: 'length', bins: Array.from({ length: 31 }, (_, i) => i * 10), a: Array.from({ length: 30 }, (_, i) => (i < 15 ? 600 - i * 30 : 40)), b: Array.from({ length: 30 }, (_, i) => (i < 15 ? 1300 - i * 50 : 90)), n_a: 10914, n_b: 25000 }],
  cached: false,
};

const RECIPES = [
  { id: 'rec-1', name: 'humor-curation', description: 'Band, balance, split', archived: false, head_revision_id: 'rev-1', head_hash: 'b'.repeat(64), step_count: 3, providers: ['native'], revision_count: 2, versions_built: 2, created_by: 'Ada', created_at: '2026-10-06T12:00:00Z', updated_at: '2026-10-06T12:00:00Z' },
  { id: 'rec-2', name: 'format-balanced', description: null, archived: false, head_revision_id: 'rev-2', head_hash: '7'.repeat(64), step_count: 4, providers: ['native', 'datajuicer'], revision_count: 1, versions_built: 1, created_by: 'Ada', created_at: '2026-10-06T12:00:00Z', updated_at: '2026-10-06T12:00:00Z' },
];

export const DRAFT = { id: 'draft-1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'goal', choices: {} }, updated_by: 'Ada', updated_at: '' };

/** Routes for feature 002; `seen` records the calls for assertions. */
export function api002(seen: Array<{ method: string; path: string; body: unknown }>): Extra {
  return (path, method, url, body) => {
    seen.push({ method, path, body });
    if (path === '/api/v1/datasets/meta') return META;
    if (path === '/api/v1/datasets' && method === 'GET') return { items: [{ id: 'd-1', name: 'humor-jev9b', target_type: 'detector', description: null, head_number: 2, head_version_id: 'v-2', parent_version_id: 'v-1', versions: 2, rows: 10914, bytes: 6220980, warnings_count: 0, state: 'ready', created_by: 'Ada', created_at: '' }], total: 1 };
    if (path === '/api/v1/datasets' && method === 'POST') return { status: 201, body: { id: 'd-2', name: (body as { name: string }).name, target_type: (body as { target_type: string }).target_type, version_list: [] } };
    if (path === '/api/v1/versions') return { items: [summary(V2), summary(V1)], total: 2 };
    if (path === '/api/v1/versions/v-2') return V2;
    if (path === '/api/v1/versions/v-1') return V1;
    if (path === '/api/v1/versions/v-2/lineage') return { version_id: 'v-2', inputs: [{ kind: 'version', version_id: 'v-1', number: 1, state: 'completed' }], parent_version_id: 'v-1', children: [], steps: [{ index: 0, execution_id: 'x0', kind: 'assemble', operator: null, operator_version: null, identity: 'a'.repeat(64), reused: true, step_seed: null, rows_in: 25000 }, ...steps.map((s) => ({ index: s.step_index, execution_id: `x${s.step_index}`, kind: 'operator', operator: s.operator, operator_version: '1', identity: 'a'.repeat(64), reused: s.reused, step_seed: 1, rows_in: s.rows_in }))], held_out_origin_version_id: 'v-2', recipe_hash: 'b'.repeat(64), recipe_revision_id: 'rev-1', seed: 20261005, rowkey_scheme: 'dw.rowkey/v1', splits: V2.splits };
    if (path === '/api/v1/versions/v-2/rows') return { items: [{ _dw_row_key: 'a1b2c3d4e5f6'.padEnd(64, '0'), _dw_occurrence: 0, _dw_split: 'train', text: 'I told my wife she was drawing her eyebrows too high. She looked surprised.' }, { _dw_row_key: 'b2c3d4e5f6a7'.padEnd(64, '0'), _dw_occurrence: 0, _dw_split: 'test', text: 'U.S. economy grows 1.2 percent in second quarter' }], total: 10914, page: 1, limit: 50, columns: [] };
    if (path === '/api/v1/versions/v-2/rows/history') return { query: url.searchParams.get('q'), results: [DROPPED] };
    if (path === '/api/v1/versions/v-2/compare') return COMPARE;
    if (path === '/api/v1/recipes' && method === 'GET') return { items: RECIPES, total: 2 };
    if (path === '/api/v1/recipes/validate') return { valid: false, body_errors: [], steps: [{ index: 1, operator: 'text_length_filter', version: '1.2', errors: [{ code: 'version_unavailable', message: 'This recipe pins text_length_filter 1.2, which is not installed. Install it, or use "Clone recipe with current operators" to move to text_length_filter 1.3.' }] }] };
    if (path === '/api/v1/recipe-drafts' && method === 'POST') return { status: 201, body: DRAFT };
    if (path === '/api/v1/recipe-drafts/draft-1' && method === 'PUT') return { ...DRAFT, ...(body as object) };
    if (path === '/api/v1/recipe-drafts/draft-1') return DRAFT;
    if (path === '/api/v1/datasets/d-2' && method === 'PATCH') return { id: 'd-2', name: 'humor', target_type: 'detector', version_list: [] };
    return undefined;
  };
}

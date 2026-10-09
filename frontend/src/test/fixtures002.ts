// Shapes of feature 002's responses for component and store tests (copied from real API output).
import type { CompareReport, DatasetsMeta, DatasetSummary, RowHistory, Version } from '@/types/versions';
import type { Recipe, RecipeSummary } from '@/types/recipes';

export const META: DatasetsMeta = {
  target_types: ['sft', 'dpo', 'kto', 'grpo_prompt', 'prm', 'detector', 'untyped'],
  default_content_columns: { sft: ['messages', 'text'], dpo: ['prompt', 'chosen', 'rejected'], kto: ['prompt', 'completion'], grpo_prompt: ['prompt'], prm: ['prompt', 'completions'], detector: [], untyped: [] },
  event_kinds: ['dropped', 'changed', 'added', 'split_assigned'],
  version_states: ['completed', 'deleted'],
  input_kinds: ['source', 'version'],
  column_roles: ['content', 'metadata', 'system'],
  rowkey_schemes: ['dw.rowkey/v1'],
  default_rowkey_scheme: 'dw.rowkey/v1',
  binding_kinds: ['label_run', 'generation_run'],
  guided_steps: ['import', 'goal', 'profile', 'curate', 'label', 'assemble', 'export'],
};

export const KEY = 'a'.repeat(64);

export const VERSION: Version = {
  id: 'v-2', dataset_id: 'd-1', dataset_name: 'humor-jev9b', target_type: 'detector', number: 2, state: 'completed',
  is_head: true, superseded_by: null, parent_version_id: 'v-1', total_rows: 10914, total_bytes: 6250000,
  recipe_hash: 'b'.repeat(64), seed: 20261005, created_by: 'Ada', created_at: '2026-10-06T12:00:00Z',
  request_digest: 'c'.repeat(64), inputs: [{ kind: 'version', version_id: 'v-1' }], recipe_revision_id: 'rev-1', recipe_id: 'rec-1',
  bindings: [], rowkey_scheme: 'dw.rowkey/v1', column_roles: { text: 'content', label: 'metadata', _dw_row_key: 'system' },
  splits: [
    { name: 'train', held_out: false, rows: 9824, bytes: 5600000, file_sha256: 'd'.repeat(64), logical_digest: 'e'.repeat(64), path: 'versions/v-2/train.parquet' },
    { name: 'test', held_out: true, rows: 1090, bytes: 650000, file_sha256: 'f'.repeat(64), logical_digest: '1'.repeat(64), path: 'versions/v-2/test.parquet' },
  ],
  held_out_origin_version_id: 'v-2', warnings: [], manifest_sha256: '2'.repeat(64), build_job_id: 'job_9', created_by_origin: 'operator',
  drop_summary: [
    { step_index: 1, operator: 'threshold_labeler', operator_version: '1', reused: true, rows_in: 25000, rows_out: 11805, dropped: 13195, changed: 0, added: 0, split_assigned: 0, reasons: [{ kind: 'dropped', reason_code: 'inside_band', count: 13195, example: '0.20 < P < 0.50' }] },
    { step_index: 2, operator: 'balance', operator_version: '1', reused: false, rows_in: 11805, rows_out: 10914, dropped: 891, changed: 0, added: 0, split_assigned: 0, reasons: [{ kind: 'dropped', reason_code: 'balance', count: 891, example: 'seed 20261005' }] },
    { step_index: 3, operator: 'split', operator_version: '1', reused: false, rows_in: 10914, rows_out: 10914, dropped: 0, changed: 0, added: 0, split_assigned: 10914, reasons: [] },
  ],
};

export const DATASET: DatasetSummary = {
  id: 'd-1', name: 'humor-jev9b', target_type: 'detector', description: null, head_number: 2, head_version_id: 'v-2',
  parent_version_id: 'v-1', versions: 2, rows: 10914, bytes: 6250000, warnings_count: 1, state: 'ready', created_by: 'Ada', created_at: '2026-10-06T12:00:00Z',
};

export const SUMMARY: RecipeSummary = {
  id: 'rec-1', name: 'humor-curation', description: 'band then balance', archived: false, head_revision_id: 'rev-1', head_hash: 'b'.repeat(64),
  step_count: 2, providers: ['native'], revision_count: 3, versions_built: 4, created_by: 'Ada', created_at: '2026-10-06T12:00:00Z', updated_at: '2026-10-06T12:00:00Z',
};

export const RECIPE: Recipe = {
  ...SUMMARY,
  revisions: [{
    id: 'rev-1', recipe_id: 'rec-1', revision_number: 1, recipe_hash: 'b'.repeat(64),
    body: { format: 'dw.recipe/v1', steps: [{ operator: 'stub_drop_short', version: '1', params: { min_len: 8 } }, { operator: 'stub_keep', version: '1', params: {} }] },
    step_labels: ['short', 'all'], cloned_from_revision_id: null, imported: false, created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-06T12:00:00Z', versions_built: 4,
  }],
};

export const DROPPED: RowHistory = {
  row_key: KEY, status: 'dropped', present_in: [], searched: ['v-1', 'v-2'], version_deleted: false,
  origin: { source_id: 'src-12345678', source_locator: 'train:6' },
  dropped_at: { version_id: 'v-1', version_number: 1, step_index: 1, operator: 'stub_drop_short', operator_version: '1', kind: 'dropped', from_key: KEY, to_key: null, reason_code: 'too_short', reason: 'text has 5 characters, below 8', statistic_name: 'length', statistic_value: 5, statistic_text: null, threshold: { value: 8, comparator: '<' } },
  trail: [],
};

export const COMPARE: CompareReport = {
  version_a: { id: 'v-2', number: 2, total_rows: 9 }, version_b: { id: 'v-1', number: 1, total_rows: 10 },
  splits: [{ split: 'train', rows_a: 9, rows_b: 10, difference: -1, label_balance: {} }],
  keys: { added: 0, removed: 1, kept: 0, changed: 8, n_a: 8, n_b: 9 },
  drop_log: [{ operator: 'stub_drop_short', reason_code: 'too_short', a: 1, b: 0, difference: 1 }],
  distributions: [{ column: 'text', kind: 'length', bins: [5, 6, 7], a: [1, 8], b: [2, 8], n_a: 9, n_b: 10 }],
  cached: false,
};

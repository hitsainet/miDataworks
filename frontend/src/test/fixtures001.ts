// Feature 001 fixtures. Fields deliberately DISAGREE where production could confuse them: the
// pinned commit differs from the head, the multi-config repository has no config chosen, and the
// detail's splits differ in size.
import type { HfPreview, SourceDetail, SourcesMeta, SourceSummary } from '@/types/sources';

export const COMMIT = '2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac';
export const HEAD = '9999999999999999999999999999999999999999';

export const META: SourcesMeta = {
  kinds: ['hf', 'upload'],
  states: ['importing', 'ready', 'failed', 'cancelled', 'deleted'],
  annotation_kinds: ['terms', 'licence', 'detection_override'],
  redistribution: ['permits', 'private_only', 'forbids'],
  chat_formats: ['plain_text', 'role_content', 'from_value', 'unknown'],
  trl_types: ['language_modeling', 'preference', 'none', 'undetected'],
  target_types: ['sft', 'dpo', 'detector'],
  csv_defaults: { delimiter: ',', quote: '"', header: true, encoding: 'utf-8', type_mode: 'infer' },
  limits: { upload_max_bytes: 2 * 1024 ** 3, import_confirm_bytes: 50e9, preview_sample_rows: 100 },
};

export const DETECTION = {
  detector_version: 'dw.detect/v1',
  trl_type: 'none',
  trl_format: null,
  chat_format: 'plain_text',
  text_columns: ['text'],
  label_columns: ['humor'],
  suggested_target: 'detector',
  reasons: [{ output: 'trl_type', reason: 'Columns text and humor: plain rows with a label.' }],
};

export const PREVIEW: HfPreview = {
  repo_id: 'CreativeLang/ColBERT_Humor_Detection',
  requested_ref: 'main',
  resolved_commit: COMMIT,
  head_commit: HEAD,
  viewer_commit_note: 'The Dataset Viewer shows the branch head 99999999, not the pinned commit.',
  configs: ['default'],
  config: 'default',
  splits: [{ name: 'train', rows: 200000, bytes: 10920997 }],
  split: null,
  columns: [{ name: 'text', type: 'string' }, { name: 'humor', type: 'bool' }],
  sample_rows: [{ text: { truncated: true, text: 'A very long joke', length: 5000 }, humor: true }, { text: 'short', humor: false }],
  licence: { raw: 'cc-by-2.0', display: 'cc-by-2.0', origin: 'card_data' },
  gated: 'false',
  size: { num_rows: 200000, num_bytes_parquet_files: 10920997 },
  detection: DETECTION,
  unavailable: [{ part: 'size', reason: 'The Dataset Viewer could not report the size.' }],
};

export const MULTI_CONFIG: HfPreview = { ...PREVIEW, repo_id: 'tasksource/humicroedit', configs: ['subtask-1', 'subtask-2'], config: null, splits: [], sample_rows: [], detection: null, viewer_commit_note: null, unavailable: [] };

export const SOURCE: SourceSummary = {
  id: '11111111-1111-1111-1111-111111111111',
  kind: 'hf',
  state: 'ready',
  display_name: 'CreativeLang/ColBERT_Humor_Detection',
  repo_id: 'CreativeLang/ColBERT_Humor_Detection',
  config: null,
  split_selection: null,
  requested_ref: 'main',
  resolved_commit: COMMIT,
  content_hash: null,
  licence_display: 'cc-by-2.0',
  licence_origin: 'card_data',
  gated: 'false',
  token_tier: 'none',
  rows: 200000,
  splits: ['train'],
  suggested_target: 'detector',
  import_job_id: 'job_1',
  created_by: 'Ada',
  created_at: '2026-10-06T12:00:00Z',
  ready_at: '2026-10-06T12:05:00Z',
};

export const UPLOAD_SOURCE: SourceSummary = { ...SOURCE, id: '22222222-2222-2222-2222-222222222222', kind: 'upload', state: 'importing', display_name: 'jokes.parquet', repo_id: null, resolved_commit: null, content_hash: 'ab'.repeat(32), licence_display: 'not stated', licence_origin: 'none', rows: 0, splits: [], suggested_target: null };

export const DETAIL: SourceDetail = {
  ...SOURCE,
  files: [{ split: 'train', path: 'sources/x/train.parquet', rows: 200000, bytes: 10920997, sha256: 'cd'.repeat(32), columns: [{ name: 'text', type: 'string' }, { name: 'humor', type: 'bool' }], original_name: null, original_sha256: null }],
  licence: { raw: 'cc-by-2.0', display: 'cc-by-2.0', origin: 'card_data', gated: 'false', redistribution: null, terms_status: 'not recorded', history: [] },
  detection: DETECTION,
  library_versions: { datasets: '3.0' },
  error: null,
  deleted_by: null,
  deleted_at: null,
};

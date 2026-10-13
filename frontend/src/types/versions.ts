// Feature 002's wire types (FTDD 002 section 5). Mirrors backend/src/schemas/{datasets,versions}.py.

export interface DatasetsMeta {
  target_types: string[];
  default_content_columns: Record<string, string[]>;
  event_kinds: string[];
  version_states: string[];
  input_kinds: string[];
  column_roles: string[];
  rowkey_schemes: string[];
  default_rowkey_scheme: string;
  binding_kinds: string[];
  guided_steps: string[];
}

export interface DatasetSummary {
  id: string;
  name: string;
  target_type: string;
  description: string | null;
  head_number: number | null;
  head_version_id: string | null;
  parent_version_id: string | null;
  versions: number;
  rows: number | null;
  bytes: number | null;
  warnings_count: number;
  state: string;
  created_by: string;
  created_at: string;
}

export interface VersionSummary {
  id: string;
  dataset_id: string;
  dataset_name: string;
  target_type: string;
  number: number;
  state: string;
  is_head: boolean;
  superseded_by: number | null;
  parent_version_id: string | null;
  total_rows: number;
  total_bytes: number;
  warnings_count: number;
  recipe_hash: string;
  seed: number;
  created_by: string;
  created_at: string;
}

export interface Dataset extends DatasetSummary {
  version_list: VersionSummary[];
}

export interface SplitInfo {
  name: string;
  held_out: boolean;
  rows: number;
  bytes: number;
  file_sha256: string;
  logical_digest: string;
  path: string;
}

export interface ReasonCount {
  kind: string;
  reason_code: string;
  count: number;
  example: string | null;
}

export interface DropStep {
  step_index: number;
  operator: string;
  operator_version: string;
  reused: boolean;
  rows_in: number;
  rows_out: number;
  dropped: number;
  changed: number;
  added: number;
  split_assigned: number;
  reasons: ReasonCount[];
}

export interface VersionWarning {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Version extends Omit<VersionSummary, 'warnings_count'> {
  request_digest: string;
  inputs: Array<Record<string, unknown>>;
  recipe_revision_id: string;
  recipe_id: string | null;
  bindings: Array<{ kind: string; id: string }>;
  rowkey_scheme: string;
  column_roles: Record<string, string>;
  splits: SplitInfo[];
  held_out_origin_version_id: string | null;
  warnings: VersionWarning[];
  drop_summary: DropStep[];
  manifest_sha256: string;
  build_job_id: string;
  created_by_origin: string;
  deleted_by?: string | null;
  deleted_at?: string | null;
  delete_reason?: string | null;
}

export interface BuildAccepted {
  job_id: string;
  existing_job: boolean;
  seed: number;
  request_digest: string;
}

export type InputRef = { kind: 'source'; source_id: string } | { kind: 'version'; version_id: string };

export interface BuildRequest {
  dataset_id: string;
  inputs: InputRef[];
  seed?: number | null;
  bindings?: Array<{ kind: string; id: string }>;
  column_roles?: Record<string, 'content' | 'metadata'>;
}

export interface RowPage {
  items: Array<Record<string, unknown>>;
  total: number;
  page: number;
  limit: number;
  columns: string[];
}

export interface TrailEntry {
  version_id: string;
  version_number: number;
  step_index: number;
  operator: string | null;
  operator_version: string | null;
  kind: string;
  from_key: string;
  to_key: string | null;
  reason_code: string;
  reason: string;
  statistic_name: string | null;
  statistic_value: number | null;
  statistic_text: string | null;
  threshold: Record<string, unknown> | null;
}

export interface RowHistory {
  row_key: string;
  status: 'present' | 'dropped' | 'not_found';
  present_in: Array<{ version_id: string; split: string; occurrence: number }>;
  dropped_at: TrailEntry | null;
  trail: TrailEntry[];
  origin: { source_id?: string; source_locator?: string; generated?: boolean; parent_keys?: string[] } | null;
  searched: string[];
  version_deleted: boolean;
}

export interface Lineage {
  version_id: string;
  inputs: Array<Record<string, unknown>>;
  parent_version_id: string | null;
  children: Array<{ version_id: string; number: number; state: string }>;
  steps: Array<{
    index: number;
    execution_id: string;
    kind: string;
    operator: string | null;
    operator_version: string | null;
    identity: string;
    reused: boolean;
    step_seed: number | null;
    rows_in: number | null;
  }>;
  held_out_origin_version_id: string | null;
  recipe_hash: string;
  recipe_revision_id: string;
  seed: number;
  rowkey_scheme: string;
  splits: SplitInfo[];
}

export interface Histogram {
  column: string;
  kind: 'length' | 'numeric';
  bins: number[];
  a: number[];
  b: number[];
  n_a: number;
  n_b: number;
}

export interface TopValues {
  column: string;
  kind: 'categorical';
  values: string[];
  a: Record<string, number>;
  b: Record<string, number>;
  n_a: number;
  n_b: number;
}

export interface CompareReport {
  version_a: { id: string; number: number; total_rows: number };
  version_b: { id: string; number: number; total_rows: number };
  splits: Array<{
    split: string;
    rows_a: number;
    rows_b: number;
    difference: number;
    label_balance: Record<string, { a: Record<string, number>; b: Record<string, number> }>;
  }>;
  keys: { added: number; removed: number; kept: number; changed: number; n_a: number; n_b: number };
  drop_log: Array<{ operator: string; reason_code: string; a: number; b: number; difference: number }>;
  distributions: Array<Histogram | TopValues>;
  cached: boolean;
}

// Feature 001 wire types (001 FTDD section 5.2). Vocabularies are NOT duplicated here as unions:
// they come from GET /api/v1/sources/meta at run time, so a backend change needs no edit here.

export interface DetectionReason {
  output: string;
  reason: string;
}

export interface Detection {
  detector_version: string;
  trl_type: string;
  trl_format: string | null;
  chat_format: string;
  text_columns: string[];
  label_columns: string[];
  suggested_target: string;
  reasons: DetectionReason[];
  overridden?: boolean;
  suggested?: Detection;
}

export interface TruncatedCell {
  truncated: true;
  text: string;
  length: number;
}

export interface HfPreview {
  repo_id: string;
  requested_ref: string | null;
  resolved_commit: string;
  head_commit: string | null;
  viewer_commit_note: string | null;
  configs: string[];
  config: string | null;
  splits: Array<{ name: string; rows: number | null; bytes: number | null }>;
  split: string | null;
  columns: Array<{ name: string; type: string }>;
  sample_rows: Array<Record<string, unknown>>;
  licence: { raw: string | null; display: string; origin: string | null };
  gated: string | null;
  size: { num_rows?: number | null; num_bytes_parquet_files?: number | null } | null;
  detection: Detection | null;
  unavailable: Array<{ part: string; reason: string }>;
}

export interface HfRequest {
  repo_id: string;
  config?: string;
  split?: string;
  revision?: string;
}

export interface ImportAccepted {
  job_id: string;
  source_id: string | null;
  existing_job: boolean;
}

export interface ApprovalPending {
  approval_id: string;
  status: string;
  action: string;
  hint: string;
}

export interface SourceSummary {
  id: string;
  kind: string;
  state: string;
  display_name: string;
  repo_id: string | null;
  config: string | null;
  split_selection: string | null;
  requested_ref: string | null;
  resolved_commit: string | null;
  content_hash: string | null;
  licence_display: string;
  licence_origin: string | null;
  gated: string | null;
  token_tier: string | null;
  rows: number;
  splits: string[];
  suggested_target: string | null;
  import_job_id: string | null;
  created_by: string;
  created_at: string;
  ready_at: string | null;
}

export interface SourceAnnotation {
  id: string;
  kind: string;
  redistribution: string | null;
  value: Record<string, unknown>;
  reason: string;
  created_by: string;
  created_by_origin: string;
  approval_id: string | null;
  approved_by: string | null;
  created_at: string | null;
}

export interface SourceFile {
  split: string;
  path: string;
  rows: number;
  bytes: number;
  sha256: string;
  columns: Array<{ name: string; type: string }>;
  original_name: string | null;
  original_sha256: string | null;
}

export interface SourceDetail extends SourceSummary {
  files: SourceFile[];
  licence: {
    raw: string | null;
    display: string;
    origin: string | null;
    gated: string | null;
    redistribution: string | null;
    terms_status: string;
    history: SourceAnnotation[];
  };
  detection: Detection | null;
  library_versions: Record<string, string>;
  error: { code?: string; message?: string } | null;
  deleted_by: string | null;
  deleted_at: string | null;
}

export interface SourcesMeta {
  kinds: string[];
  states: string[];
  annotation_kinds: string[];
  redistribution: string[];
  chat_formats: string[];
  trl_types: string[];
  target_types: string[];
  csv_defaults: CsvOptions;
  limits: { upload_max_bytes: number; import_confirm_bytes: number; preview_sample_rows: number };
}

export interface CsvOptions {
  delimiter: string;
  quote: string;
  header: boolean;
  encoding: string;
  type_mode: 'infer' | 'text';
}

export interface UploadManifest {
  files: Array<{ name: string; split: string }>;
  csv?: CsvOptions;
  display_name?: string;
}

export interface AnnotationRequest {
  kind: string;
  redistribution?: string | null;
  value?: Record<string, unknown>;
  reason: string;
}

/** A tracked import: from the room while connected, from the job record while not. */
export interface ImportProgress {
  jobId: string;
  label: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  phase: string | null;
  progress: number;
  sourceId: string | null;
  existing: boolean;
  error: string | null;
}

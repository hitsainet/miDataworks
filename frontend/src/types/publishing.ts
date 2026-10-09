// Feature 008's wire types. Mirrors backend/src/schemas/publishing.py.

export type Visibility = 'private' | 'public';
export type OutcomeLevel = 'green' | 'note' | 'amber' | 'refused';

export interface SplitFile {
  name: string;
  path: string;
  rows: number;
  bytes: number;
  sha256: string;
  git_blob_sha1: string;
  logical_digest: string;
  label_counts: Record<string, number>;
  held_out: boolean;
  evaluation_only: boolean;
}

export interface PublishBuild {
  id: string;
  version_id: string;
  status: string;
  projection: Record<string, unknown>;
  files: SplitFile[] | null;
  columns: Array<Record<string, unknown>> | null;
  omitted: Record<string, number> | null;
  error: { code: string; message: string } | null;
  job_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface BuildAccepted {
  build_id: string;
  job_id: string | null;
  reused: boolean;
  status: string;
}

export interface CheckOutcome {
  check: string;
  outcome: OutcomeLevel;
  reason: string;
  next_step: string;
  evidence: Record<string, unknown>;
}

export interface CheckRun {
  id: string;
  version_id: string;
  build_id: string;
  repo_id: string;
  requested_visibility: Visibility;
  status: string;
  results: CheckOutcome[] | null;
  licence_table_version: number | null;
  job_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface PublishFileRow {
  path: string;
  role: string;
  split: string | null;
  bytes: number;
  sha256: string;
  git_blob_sha1: string;
  remote_lfs_sha256: string | null;
  remote_blob_id: string | null;
  match: boolean | null;
}

export interface PublishRecord {
  id: string;
  job_id: string;
  version_id: string;
  build_id: string;
  kind: string;
  repo_id: string;
  requested_visibility: Visibility;
  visibility_after: Visibility | null;
  commit: string | null;
  status: string;
  check_snapshot: CheckOutcome[] | null;
  started_by: string;
  started_by_origin: string;
  send_id: string | null;
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
  created_at: string;
  completed_at: string | null;
  files: PublishFileRow[];
}

export interface PublishAccepted {
  publish_id: string;
  job_id: string;
  request_digest: string;
}

export interface CardDraft {
  front_matter: Record<string, unknown>;
  record_markdown: string;
  prose: string;
  build_id: string;
}

export interface ExportRecord {
  id: string;
  job_id: string | null;
  target: string;
  version_id: string | null;
  params: Record<string, unknown>;
  trl_version: string | null;
  status: string;
  files: Array<{ path: string; role: string; split: string; bytes: number; sha256: string }> | null;
  manifest_sha256: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
}

export interface ExportRequest {
  target: 'trl' | 'miforge_set';
  version_id: string;
  trl_type?: string;
  miforge_set_kind?: string;
  format: 'parquet' | 'jsonl';
  label_column?: string | null;
}

export interface TermsNote {
  id: string;
  model_id: string;
  training_on_outputs: 'permits' | 'forbids';
  text: string;
  noted_by: string;
  noted_at: string;
}

export interface ModelTerms {
  model_id: string;
  notes: TermsNote[];
  latest: 'permits' | 'forbids' | null;
}

export interface ActiveJob {
  jobId: string;
  kind: 'publish_build' | 'publish_check' | 'publish' | 'export' | 'publish_reverify';
  status: string;
  progress: number;
  message: string | null;
}

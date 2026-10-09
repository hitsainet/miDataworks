// Feature 007's wire types (FTDD 007 section 5). Mirrors backend/src/schemas/generation.py.

export type SteeringSetting =
  | { kind: 'none' }
  | { kind: 'profile'; profile_name: string }
  | { kind: 'inline'; sae_id: string; features: Array<{ index: number; strength: number }> };

export interface GenerationTemplate {
  id: string;
  name: string;
  version: number;
  ref: string;
  kind: 'expand' | 'respond';
  description: string | null;
  body: Record<string, unknown>;
  content_hash: string;
  builtin: boolean;
  used: boolean;
  placeholders: string[];
  cloned_from?: string | null;
}

/** A template's body (TemplateBody): what is sent to the endpoint for each row. */
export interface TemplateBody {
  prompt: string;
  system?: string | null;
  sampling: { temperature: number; top_p: number; max_tokens: number };
  structured_output: 'none' | 'json_schema';
  json_schema?: Record<string, unknown> | null;
}

export interface TemplateCreate {
  name: string;
  kind: 'expand' | 'respond';
  description?: string | null;
  body: TemplateBody;
}

/** A clone keeps the source's name and kind and takes the next version. */
export interface TemplateClone {
  body?: TemplateBody | null;
  description?: string | null;
}

export interface Snapshot {
  side: string;
  kind: string;
  profile_name: string | null;
  profile_updated_at: string | null;
  intensity: number | null;
  model_id: string | null;
  sae_id: string | null;
  layer: number | null;
  features: number[][];
  sent_features: number[][];
  set_hash: string | null;
}

export interface Identity {
  model_id: string;
  revision: string;
  set_hash: string;
}

export interface RunCreate {
  mode: 'standard' | 'steered_pairs' | 'minimal_pairs';
  input_version_id: string;
  prompt_column: string;
  seed_splits: string[];
  sample_size: number;
  seed?: number | null;
  n_responses?: number;
  expand_template_id?: string | null;
  respond_template_id?: string | null;
  generator_setting?: SteeringSetting;
  setting_a?: SteeringSetting;
  setting_b?: SteeringSetting;
  chosen_side?: 'a' | 'b';
  target_type: 'sft' | 'kto' | 'grpo_prompt' | 'dpo';
}

export interface Plan {
  mode: string;
  target_type: string;
  held_out: { present: boolean; splits: string[]; origin_version_id: string | null };
  seed_rows_available: number;
  seed_rows_selected: number;
  responses_per_prompt: number;
  expected_requests: number;
  stages: string[];
  engine_path: string;
  server_kind: string;
  resident_model: string | null;
  model_revision: string | null;
  snapshots: Snapshot[];
  differing_index: number | null;
  generator_identities: Identity[];
  judge_identity: Identity | null;
  independence: 'independent' | 'not_checked';
  pinned_expected: boolean | null;
  warnings: Array<{ code: string; message: string }>;
}

export interface GenerationRun {
  id: string;
  mode: 'standard' | 'steered_pairs' | 'minimal_pairs';
  target_type: string;
  state: string;
  input_version_id: string;
  held_out_splits: string[];
  prompt_column: string;
  seed_splits: string[];
  sample_size: number;
  seed: number;
  n_responses: number;
  stages: string[];
  generation_endpoint: Record<string, unknown>;
  server_kind: string;
  generator_identities: Identity[];
  judge_identity: Identity | null;
  chosen_side: string | null;
  engine_path: string;
  pinned: boolean | null;
  revision_reported: boolean | null;
  model_revision: string | null;
  failure_reason: string | null;
  error: { code: string; message: string } | null;
  warnings: Array<{ code: string; message: string }>;
  counts: Record<string, number>;
  snapshots: Snapshot[];
  started_by: string;
  started_by_origin: string;
  created_at: string;
  completed_at: string | null;
  job_ids: string[];
  current_job_id: string | null;
  room: string;
  resumable: boolean;
}

export interface GenerationRecord {
  stage: string;
  record_index: number;
  side: string | null;
  model_id: string | null;
  requested_set_hash: string | null;
  reported_steering: string | null;
  steering_check: string;
  check_reasons: string[];
  seed_sent: number | null;
  seed_confirmed: boolean | null;
  outcome: string;
  reason_code: string | null;
  prompt: string | null;
  text: string | null;
}

export interface GenerationPair {
  prompt_row_key: string;
  pair_index: number;
  chosen_side: string;
  shared_seed: number;
  prompt: string | null;
  text_a: string | null;
  text_b: string | null;
  steering_a: string | null;
  steering_b: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
}

export interface CompareResult {
  one_axis: boolean;
  differing: Array<{ index: number; a: number; b: number }>;
  differing_index: number | null;
  not_comparable: string[];
  message: string;
  code: string | null;
}

export interface IndependenceResult {
  independent: boolean | null;
  judge_identity: Identity | null;
  generator_identities: Identity[];
  conflicts: unknown[];
  inherited_from: string | null;
  message: string;
}

export interface PreviewItem {
  prompt: string;
  text: string | null;
  model: string | null;
  reported_steering: string | null;
  steering_check: string;
  check_reasons: string[];
  error: string | null;
}

export interface Preview {
  items: PreviewItem[];
  server_kind: string;
  model_id: string;
  stopped_early: boolean;
}

export interface Interval {
  value: number | null;
  lo: number | null;
  hi: number | null;
}

export interface Figure {
  version: Interval | null;
  reference: Interval | null;
  verdict: 'holds' | 'falls' | 'not_measured';
  reason: string | null;
}

export interface DiversityReport {
  id: string;
  version_id: string;
  reference_version_id: string | null;
  column: string;
  splits: string[];
  sample_size: number;
  embedding_identity: { served_model?: string | null } | null;
  clustering: { basis?: string | null; k?: number | null };
  figures: Record<string, Figure>;
  checks: Array<{ check_id: string; result: string; reason: string | null }>;
  verdict: 'holds' | 'falls' | 'not_measured' | 'invalid';
  reason: string | null;
  created_at: string;
}

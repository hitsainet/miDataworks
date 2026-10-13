// Feature 005 types, mirroring backend/src/schemas/labeling.py (the label-run contract, FPRD 005
// section 7.4). Field names are the API's; nothing is renamed on the way in.

export type LabelRole = 'classifier' | 'judge' | 'probe';
/** miLLM's probe windows (009). `all` reproduces miStudio's own scope. */
export type ProbeWindow = 'all' | 'prompt' | 'response' | 'last_user';
export type RunState = 'awaiting_approval' | 'queued' | 'running' | 'cancelled' | 'completed' | 'failed' | 'rejected';

export interface DecisionTemplate {
  id: string;
  name: string;
  version: number;
  ref: string;
  content_hash: string;
  protocol: string;
  variant: string | null;
  bound_model_id: string | null;
  bound_model_revision: string | null;
  body: Record<string, unknown> & { label_set?: string[]; input_fields?: string[]; positive_class?: string };
  used: boolean;
  created_by: string;
  created_by_origin: string;
  created_at: string;
}

export interface Rubric {
  id: string;
  name: string;
  version: number;
  ref: string;
  content_hash: string;
  style: string;
  body: Record<string, unknown> & { input_fields?: string[]; allowed_verdicts?: string[] };
  used: boolean;
  created_by: string;
  created_by_origin: string;
  created_at: string;
}

export type RubricStyle = 'pointwise' | 'pairwise' | 'binary' | 'stepwise';
export type RubricParser = 'verdict_line_v1' | 'json_v1';

/** A rubric's body (RubricBody); the backend refuses any field not listed here. */
export interface RubricBody {
  style: RubricStyle;
  messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>;
  input_fields: string[];
  axes?: string[];
  parser: RubricParser;
  allowed_verdicts: string[];
  json_schema?: Record<string, unknown> | null;
  pair_fields?: [string, string] | null;
  swap_map?: Record<string, string> | null;
}

export interface RubricCreate {
  name: string;
  body: RubricBody;
}

/** The `midataworks.rubric/v1` file an export writes and an import reads. */
export interface RubricExport {
  format: 'midataworks.rubric/v1';
  name: string;
  version: number;
  body: RubricBody;
}

export interface LabelRunStart {
  input_version_id: string;
  role: LabelRole;
  /** Probe-verdict runs only (009): the miLLM probe and the window it reads. */
  probe?: { probe_id: string; window: ProbeWindow } | null;
  template_id?: string | null;
  rubric_id?: string | null;
  question?: string | null;
  field_map: Record<string, string>;
  threshold_positive?: number | null;
  threshold_negative?: number | null;
  min_top_probability?: number | null;
  positive_label?: string | null;
  negative_label?: string | null;
  sampling?: { temperature?: number | null; seed?: number | null; max_tokens?: number | null } | null;
  chunk_size?: number | null;
  row_filter?: { origin: 'generated' | 'source' } | null;
  keep_share_job_id?: string | null;
  /** Probe-verdict runs: run the reproduction check again after the same check failed, saying why. */
  reproduction_retry_reason?: string | null;
}

/** The rows a run covers beside the distinct row keys it scores (each key once; its label applies to every copy). */
export interface RowCoverage {
  rows: number;
  row_keys: number;
  keys_with_copies: number;
  rows_in_copied_keys: number;
  copies_disagree?: { code: string; message: string; details?: unknown } | null;
}

/** The same count over a gate's or a link's rows, with keys whose copies carry both classes. */
export interface KeySummary {
  rows: number;
  row_keys: number;
  keys_with_copies: number;
  conflicting: { count: number; rows: number; keys: Array<{ row_key: string; positive: number; negative: number }> };
}

export interface Plan {
  rows_total: number;
  rows_reused: number;
  rows_to_score: number;
  agent_window_rows: number;
  approval_needed: boolean;
  threshold: number;
  labeler_identity: Record<string, unknown>;
  labeler_identity_hash: string;
  labeler_fingerprint: string;
  server_kind: string;
  resident_model: string | null;
  model_revision: string | null;
  /** Probe-verdict runs only (009); null for classifier and judge runs. */
  probe?: ProbePlan | null;
  /** Probe-verdict runs only (009): the reproduction gate this run passes through. */
  reproduction?: Reproduction | null;
  row_coverage?: RowCoverage | null;
}

/** A probe as miLLM states it (009). Strings may be the literal "not reported". */
export interface ProbeFacts {
  probe_id: string;
  name: string;
  hf_id: string;
  layer: number | string;
  scope: string;
  window: ProbeWindow;
  window_bar: { threshold: number | null; provisional: boolean };
  threshold_revision: number | string;
  rung: number | string | null;
  rung_language: string | null;
  armed: boolean | string;
  mistudio_probe_id: string | null;
  mistudio_run_id: string | null;
  load_dtype: string | null;
}

export interface ProbePreflight {
  checked: boolean;
  reason?: string;
  row_key?: string;
  score?: number | null;
  threshold?: number | null;
  verdict?: boolean | null;
  provisional?: boolean | null;
  error?: Record<string, unknown> | null;
  model?: { hf_id: string; revision: string; dtype: string; quantization: string };
  /** Which bar the threshold is: a length band of the window, or the window's own bar. */
  bar?: PreflightBar | null;
}

export interface PreflightBar {
  kind: 'length_band' | 'window' | 'not_described' | 'none';
  window: string;
  window_threshold: number | null;
  window_provisional: boolean;
  threshold: number | null;
  n_tokens: number | null;
  band?: { min_tokens: number | null; max_tokens: number | null };
  label: string;
}

/** Why the gate chose its target over the other recorded evaluations. */
export interface TargetChoice {
  rule: string;
  why: string;
  chosen: TargetBrief;
  alternatives: TargetBrief[];
  alternatives_total: number;
}

export interface TargetBrief {
  source: string;
  link_id: string | null;
  snapshot_id: string | null;
  check_level: LinkCheckLevel | null;
  role: string;
  view_name: string | null;
  version_id: string;
  split: string;
}

export interface ProbePlan extends ProbeFacts {
  preflight: ProbePreflight;
}

/** The reproduction gate (009 FR-009.77): a probe must reproduce miStudio's AUROC before labeling. */
export interface Reproduction {
  state: 'will_run' | 'passed' | 'failed';
  /** Where the target came from: a results snapshot of a send, or a reproduction link (option b). */
  source?: 'detector_results' | 'linked_mistudio_evaluation';
  link_id?: string | null;
  link_check_level?: LinkCheckLevel | null;
  scoring_form?: ScoringForm | null;
  role: string;
  view_name: string | null;
  mistudio_auroc: number;
  mistudio_ci: [number, number];
  n_rows: number;
  version_id: string;
  split: string;
  snapshot_id: string | null;
  set_id: string | null;
  send_id: string | null;
  probe_dataset_id: string;
  mistudio_probe_id: string;
  cached_from_run_id?: string | null;
  millm_auroc?: number | null;
  rows_scored?: number | null;
  rows_dropped?: number | null;
  reason?: string | null;
  row_keys?: KeySummary | null;
  choice?: TargetChoice | null;
  failed_run_id?: string | null;
  failed_at?: string | null;
  retry?: string | null;
  retry_of?: { run_id: string; millm_auroc?: number | null; reason: string } | null;
}

export type LinkCheckLevel = 'content' | 'counts_only';

/** How miStudio scored the evaluated rows against how miLLM will, and whether they are known equal.
 * `equal_by_render_rule`: miStudio's RECORDED render form renders each row as miLLM will; token ids
 * are never compared (`token_ids_compared` is false), so it never reads "verified". Links stored
 * before 2026-10-08 carry no render fields, and a `note` when their description is known wrong. */
export type ScoringAgreement = 'equal_by_render_rule' | 'not_verified' | 'differs';

export interface ScoringForm {
  mistudio: {
    input_kinds: Record<string, number>;
    scope: string;
    template_hash: string;
    max_length: number | string;
    described_as: string;
    /** As miStudio recorded it; null = not recorded (rendered without the generation prompt). */
    render_form?: Record<string, unknown> | null;
    render_form_recorded?: boolean;
    render_form_source?: 'probe' | 'run' | null;
    render_served?: boolean;
    mistudio_render_served?: boolean | null;
  };
  millm: { input_form: 'text' | 'messages'; described_as: string; last_roles?: Record<string, number> };
  agreement: ScoringAgreement;
  token_ids_compared?: boolean;
  reason: string;
  note?: string;
}

export interface LinkChecks {
  level: LinkCheckLevel | 'refused';
  row_count: { ran: boolean; ours: number; mistudio: number; passed: boolean };
  class_balance: {
    ran: boolean;
    ours: { positive: number; negative: number };
    mistudio: { positive: number; negative: number };
    passed: boolean;
  };
  content: {
    ran: boolean;
    reason: string | null;
    order: string;
    ordered_match?: boolean;
    unordered_match?: boolean;
    passed?: boolean;
    ours_sha256?: { ordered: string; unordered: string };
    mistudio_sha256?: { ordered: string; unordered: string };
  };
  row_keys?: KeySummary & { ran: boolean; passed: boolean };
}

/** An evaluation miStudio recorded for a probe, which a reproduction link can name. */
export interface LinkCandidate {
  probe_dataset_id: string;
  view_name: string | null;
  distribution: string | null;
  auroc: number;
  ci: [number, number];
  n_positive: number;
  n_negative: number;
  mistudio_dataset_id: string | null;
  split: string | null;
  config: string | null;
  input_column: string | null;
  label_column: string | null;
  label_mapping: Record<string, string> | null;
  input_kinds: Record<string, number> | null;
}

export interface ReproductionLink {
  id: string;
  mistudio_probe_id: string;
  probe_dataset_id: string;
  view_name: string | null;
  role: string;
  version_id: string;
  split: string;
  input_column: string;
  label_column: string;
  mistudio_auroc: number;
  mistudio_ci: [number, number];
  n_positive: number;
  n_negative: number;
  check_level: LinkCheckLevel;
  checks: LinkChecks;
  scoring_form: ScoringForm;
  created_by: string;
  created_at: string | null;
  used_by_label_runs: string[];
}

export interface ReproductionLinkStart {
  mistudio_probe_id: string;
  probe_dataset_id: string;
  version_id: string;
  split: string;
  input_column?: string | null;
  label_column?: string | null;
}

/** A plan refused REPRODUCTION_UNAVAILABLE: the refusal still carries what the plan learned. */
export interface ReproductionRefusal {
  message: string;
  mistudio_probe_id: string | null;
  probe: ProbePlan;
  reproduction: { state: 'unavailable'; reason: string; ways: Array<{ way: string; what: string }> };
  link_candidates: { items: LinkCandidate[]; reason: string | null };
}

export interface ProbeListItem {
  probe_id: string;
  name: string;
  /** The model the probe was fitted on. */
  hf_id: string;
  layer: number;
  scope: string;
  threshold: number | null;
  threshold_revision: number;
  window_thresholds: Record<string, number>;
  rung: number | null;
  rung_language: string | null;
  armed: boolean;
  /** Whether miLLM's resident model is the one the probe was fitted on; null = nothing loaded. */
  fits_resident_model: boolean | null;
}

export interface ProbeList {
  items: ProbeListItem[];
  resident_model: string | null;
  millm_base_url: string;
}

export interface LabelRun {
  id: string;
  kind: 'classifier' | 'judge' | 'rederived' | 'aggregate' | 'probe_verdict' | 'feature_tag';
  state: RunState;
  input_version_id: string;
  field_map: Record<string, string>;
  endpoint_snapshot: Record<string, unknown> & { model_id?: string; base_url?: string; server_kind?: string; model_revision?: string | null; protocol?: string; role?: string;
    probe?: ProbeFacts; reproduction?: Reproduction; pinned?: boolean; unpinned_reason?: string | null; millm_model?: unknown };
  template_id: string | null;
  rubric_id: string | null;
  template_ref: string | null;
  question: string | null;
  positive_label: string | null;
  negative_label: string | null;
  threshold_positive: number | null;
  threshold_negative: number | null;
  min_top_probability: number | null;
  label_set: string[] | null;
  sampling: Record<string, unknown>;
  structured_output: string;
  packing: string;
  batch_id: string | null;
  chunk_size: number;
  row_filter: Record<string, string> | null;
  parent_run_ids: string[];
  labeler_identity: Record<string, unknown>;
  labeler_identity_hash: string;
  labeler_fingerprint: string;
  pinned: boolean | null;
  revision_reported: boolean | null;
  system_fingerprint: string | null;
  counts: Record<string, number>;
  keep_share_estimate: { share: number; lo: number; hi: number; n: number; seed?: number } | null;
  keep_share_actual: number | null;
  length_correlation: { chars: { rho: number | null; n: number; reason: string | null }; tokens: { rho: number | null; n: number; reason: string | null } | null } | null;
  rows_total: number;
  rows_reused: number;
  rows_done: number;
  agent_counted_rows: number;
  approval_id: string | null;
  error: { code: string; message: string } | null;
  started_by: string;
  started_by_origin: string;
  created_at: string;
  completed_at: string | null;
  job_ids: string[];
  current_job_id: string | null;
  room: string;
  /** Whether Resume applies; a run the reproduction gate stopped is not resumable. */
  resumable?: boolean;
  not_resumable_reason?: string | null;
  row_coverage?: RowCoverage | null;
}

export interface LabelRunList {
  items: LabelRun[];
  total: number;
  page: number;
  limit: number;
}

export interface Label {
  label_run_id: string;
  row_key: string;
  labeler_fingerprint: string;
  outcome: string;
  parsed_value: unknown;
  probability: number | null;
  distribution: Record<string, number> | null;
  raw_output: unknown;
  rationale: string | null;
  steering_state: string;
  latency_ms: number | null;
  skip_reason: string | null;
  provisional: boolean;
  reused_from_run_id: string | null;
  chunk_index: number | null;
  scored_at: string;
  started_by: string;
  started_by_origin: string;
}

export interface LabelPage {
  items: Label[];
  total: number;
  page: number;
  limit: number;
}

export interface SampleRow {
  row_key: string;
  text: string;
  probability: number | null;
  distribution: Record<string, number> | null;
  outcome: string | null;
  verdict: string | null;
  rationale: string | null;
  latency_ms: number | null;
  error: string | null;
}

export interface SampleResult {
  rows: SampleRow[];
  model: string;
  server_kind: string;
  steering_state: string;
}

export interface KeepShareResult {
  job_id: string;
  status: string;
  share: number | null;
  lo: number | null;
  hi: number | null;
  n: number | null;
  seed: number | null;
  probabilities: number[] | null;
  error: string | null;
}

export interface EndpointTestResult {
  role: string;
  reachable: boolean;
  model_listed: boolean | null;
  protocol_ok: boolean | null;
  server_kind: string | null;
  resident_model: string | null;
  lease_supported: boolean | null;
  lease_state: string | null;
  queue: Record<string, number | null> | null;
  error_code: string | null;
  message: string;
}

/** 006 FR-006.20: the real status shape, owned by feature 006's types. */
export type { CalibrationStatus } from '@/types/calibration';

export interface ApprovalAccepted {
  approval_id: string;
  status: string;
  action: string;
  hint: string;
}

// Feature 009 detector sets: the shapes the backend returns (FTDD 009 section 5.1).
export type DetectorRole = 'train' | 'id_test' | 'ood_eval' | 'calibration_negatives';
export type MappingTarget = 'positive' | 'negative' | 'excluded';
export type CheckOutcomeName = 'green' | 'note' | 'refused';

export interface NegativesBasis {
  kind: 'labeler_filtered' | 'assumed_negative' | 'human_labelled';
  labeler_identity_hash?: string | null;
  rule?: string | null;
  /** human_labelled: the label column, the values that select the negatives, and who labelled them. */
  label_column?: string | null;
  negative_values?: string[] | null;
  labelled_by?: string | null;
}

export interface RoleIn {
  role: DetectorRole;
  version_id: string;
  split: string;
  input_column: string;
  label_column: string;
  label_mapping: Record<string, MappingTarget>;
  pair_column?: string | null;
  /** Columns the label was computed from: D-3's shortcut audit sets them aside and reports them. */
  label_source_columns?: string[];
  negatives_basis?: NegativesBasis | null;
  display_name?: string | null;
}

export interface DetectorSetRole extends RoleIn {
  id: string;
  role_words: string;
  position: number;
  version_number: number | null;
  version_state: string | null;
  dataset_id: string | null;
  dataset_name: string | null;
  view_name: string;
}

export interface LengthProfile {
  version_id: string;
  split: string;
  column: string;
  unit: string;
  n: number;
  quantiles: Record<string, number>;
  histogram: { edges: number[]; counts: number[] };
  finest_fpr: number | null;
}

export interface DetectorSetSummary {
  id: string;
  name: string;
  description: string;
  positive_meaning: string;
  archived: boolean;
  role_counts: Partial<Record<DetectorRole, number>>;
  last_send_state: string | null;
  last_send_id: string | null;
  last_rung: { rung: number; rung_language: string | null } | null;
  created_by: string;
  created_by_origin: string;
  created_at: string | null;
}

export interface SendSummary {
  id: string;
  set_id: string;
  state: string;
  job_id: string;
  visibility: string;
  mistudio_base_url: string;
  started_by: string;
  started_by_origin: string;
  approval_id: string | null;
  created_at: string | null;
  completed_at: string | null;
  error: Record<string, unknown> | null;
}

export interface DetectorSet extends DetectorSetSummary {
  monitored_ref: Record<string, unknown> | null;
  roles: DetectorSetRole[];
  profiles: Record<string, LengthProfile>;
  sends?: SendSummary[];
}

export interface CheckOutcome {
  code: string;
  title: string;
  outcome: CheckOutcomeName;
  figure: string | null;
  reason: string;
  next_step: string | null;
  source: string;
  details: Record<string, unknown>;
}

export interface ChecksResult {
  set_id: string;
  outcomes: CheckOutcome[];
  send_allowed: boolean;
  first_refusal: CheckOutcome | null;
  label_values: Record<string, Record<string, number>>;
  expected_counts: Record<string, Record<string, number>>;
  profiles: Record<string, LengthProfile>;
  monitored: { kind: string; profile: LengthProfile } | null;
  overlap_min: number | null;
}

export interface SendStep {
  step: 'publish' | 'download' | 'register';
  unit_key: string;
  position: number;
  role_ids: string[];
  state: 'pending' | 'running' | 'done' | 'reused' | 'failed';
  publish_id: string | null;
  repo_id: string | null;
  commit: string | null;
  mistudio_dataset_id: string | null;
  probe_dataset_id: string | null;
  expected_counts: Record<string, number> | null;
  registered_counts: Record<string, number> | null;
  error: { code: string; message: string; next_step?: string | null } | null;
}

export interface RunRequest {
  train_dataset_id: string;
  eval_dataset_ids: string[];
  calibration_dataset_id: string;
}

export interface SendDetail extends SendSummary {
  job_ids: string[];
  snapshot: { set: Record<string, unknown>; roles: DetectorSetRole[]; repositories: Record<string, string> };
  checks: CheckOutcome[];
  notes: CheckOutcome[];
  steps: SendStep[];
  run_request: RunRequest | null;
}

export interface SetFigure {
  probe_dataset_id: string;
  role?: DetectorRole;
  view_name?: string;
  auroc: number | null;
  ci: [number, number] | null;
  n_positive: number;
  n_negative: number;
  firing?: { positives_firing: number; negatives_firing: number; unreachable: boolean; n_positive: number; n_negative: number } | null;
  paired?: { available: boolean; reason?: string; paired?: number; ci?: [number, number]; pairs?: number };
  label?: string;
}

export interface ProbeFigure {
  probe_id: string;
  run_id: string;
  layer: number;
  rule: string;
  rule_params: Record<string, unknown>;
  selected: boolean;
  threshold: number;
  target_fpr: number;
  realised_fpr: number;
  rung: number;
  rung_language: string | null;
  rung_next_step: string | null;
  sets: SetFigure[];
  not_from_this_set: SetFigure[];
  caveats: { validation_caveat: Record<string, unknown> | null; threshold_transfer_caution: string | null };
  judge_runs: Array<Record<string, unknown>>;
  reward: boolean;
  gone?: boolean;
  label?: string;
}

export interface ResultsSnapshot {
  id: string;
  set_id: string;
  send_id: string;
  read_at: string | null;
  read_by: string;
  runs: Array<Record<string, unknown>>;
  evaluations: ProbeFigure[];
  training_reward: ProbeFigure[];
  gone: { runs: unknown[]; probes: string[] };
}

export interface SendStarted {
  send_id?: string;
  job_id?: string;
  approval_id?: string;
  status?: string;
}

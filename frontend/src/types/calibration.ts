// Feature 006 calibration types, mirroring backend/src/schemas/calibration.py.
// AUROC: area under the receiver operating characteristic curve. CI: confidence interval.

export type Verdict = 'passes' | 'fails' | 'invalid' | 'insufficient';
export type GateRule = 'operator_target' | 'held_out_rater' | 'default_c3';

export interface IntervalFigure {
  value: number;
  ci_low: number;
  ci_high: number;
  n?: number;
}

export interface ReliabilityBin {
  lo: number;
  hi: number;
  n: number;
  positive_fraction: number | null;
  sparse: boolean;
}

export interface BandSlice {
  n: number;
  at_or_above: number;
  at_or_below: number;
  excluded: number;
}

export interface CalibrationMetrics {
  score_kind: 'probability' | 'distribution' | 'discrete';
  n_rows: number;
  n_labeled: number;
  auroc: (IntervalFigure & { n: number; n_pos: number; n_neg: number; resamples: number; dropped: number }) | null;
  ceiling: (IntervalFigure & { draws: number; n_mean: number }) | null;
  comparison_auroc: IntervalFigure | null;
  paired: (IntervalFigure & { pairs: number; groups: number }) | null;
  reference_diagnostic: { value: number; control: number; control_ci: [number, number]; n_pos: number; n_neg: number } | null;
  reliability: ReliabilityBin[] | null;
  band_shares: { threshold_positive: number; threshold_negative: number; overall: BandSlice; by_class: Record<string, BandSlice> } | null;
  kappa: { value: number; n_kept: number } | null;
  reasons: Record<string, string>;
}

export interface CheckOut {
  check_id: string;
  check_version: number;
  metric_id: string;
  result: 'pass' | 'fail' | 'not_applicable';
  statistic: Record<string, unknown>;
  rule: string;
  reason: string | null;
}

export interface VerdictOut {
  verdict: Verdict;
  rule: GateRule | null;
  numbers: Record<string, unknown> & { compared?: number; threshold?: number; target?: number | null };
  target_id: string | null;
}

export interface CalibrationRecord {
  id: string;
  calibration_set_id: string;
  label_run_id: string;
  question: string;
  question_hash: string;
  labeler_identity: Record<string, unknown>;
  labeler_identity_hash: string;
  labeler_fingerprint: string;
  score_kind: string;
  metrics: CalibrationMetrics;
  metrics_sha256: string;
  checks: CheckOut[];
  verdict: VerdictOut;
  warnings: Array<{ kind: string; message?: string }>;
  settings: Record<string, unknown>;
  conformance: Record<string, unknown> | null;
  created_by: string;
  created_by_origin: 'operator' | 'agent';
  created_at: string;
}

export interface CalibrationMapping {
  schema: 'dw.calibration-mapping/v1';
  human_label:
    | { column: string; rule: 'numeric'; positive_at_or_above: number; negative_at_or_below: number }
    | { column: string; rule: 'categorical'; map: Record<string, string> };
  ratings?: { column: string; format: 'digit_string' | 'list'; scale?: [number, number] };
  group?: { column: string };
  strata?: string[];
  reference?: { column: string; value: unknown };
}

export interface CalibrationSetImport {
  version_id: string;
  question: string;
  label_set: string[];
  mapping: CalibrationMapping;
}

export interface CalibrationSetPreview {
  counts: Record<string, number>;
  ratings_sorted: boolean | null;
  warnings: string[];
  sample: Array<Record<string, unknown>>;
  mapping_hash: string;
}

export interface CalibrationSet {
  id: string;
  version_id: string;
  question: string;
  question_hash: string;
  label_set: string[];
  source_kind: 'imported' | 'review';
  counts: Record<string, number>;
  ratings_sorted: boolean | null;
  licence_class: string;
  created_by: string;
  created_at: string;
}

export interface CalibrationStatus {
  status: 'recorded' | 'none_recorded';
  record_id: string | null;
  verdict: Verdict | null;
  rule: GateRule | null;
  auroc: { value: number; ci_low: number; ci_high: number; n: number } | null;
  calibration_set: { id: string; licence_class: string; rows_shipped: false } | null;
}

export interface TargetOut {
  id: string;
  question_hash: string;
  question: string;
  target: number;
  set_by: string;
  set_by_origin: 'operator' | 'agent';
  approval_id: string | null;
  approved_by: string | null;
  created_at: string;
}

export interface TargetsOut {
  question_hash: string;
  current: TargetOut | null;
  default_lower_bound: number;
  history: TargetOut[];
}

export interface JobStarted {
  job_id: string;
  room: string;
}

// Feature 006 review types, mirroring backend/src/schemas/review.py.

export type QueueKind = 'label_review' | 'calibration_labeling' | 'audit' | 'external';
export type DecisionKind = 'accept' | 'override' | 'flag' | 'reject';

export interface ReviewQueue {
  id: string;
  kind: QueueKind;
  version_id: string | null;
  label_run_id: string | null;
  question: string;
  question_hash: string;
  label_set: string[];
  show_model_output: boolean;
  sample_spec: Record<string, unknown> | null;
  origin_app: string | null;
  external_ref: Record<string, unknown> | null;
  state: 'open' | 'closed';
  created_by: string;
  created_by_origin: 'operator' | 'agent';
  created_at: string;
  items: number;
  decided: number;
}

export interface Decision {
  id: string;
  item_id: string;
  queue_id: string;
  row_key: string | null;
  decision: DecisionKind;
  override_label: string | null;
  reason: string;
  decided_by: string;
  decided_by_origin: 'operator' | 'agent';
  model_output_visible: boolean;
  version_id: string | null;
  created_at: string;
}

export interface ModelSnapshot {
  label_run_id?: string;
  outcome?: string | null;
  label?: string | null;
  probability?: number | null;
  rationale?: string | null;
}

export interface ReviewItem {
  id: string;
  queue_id: string;
  position: number;
  row_key: string | null;
  external_id: string | null;
  payload: { prompt?: string; completion?: string; model_output?: unknown; provenance?: Record<string, unknown> } | null;
  model_snapshot: ModelSnapshot | null;
  model_output_hidden: boolean;
  stratum: string | null;
  text: Record<string, string | null> | null;
  latest_decision: Decision | null;
  /** Client-only: an optimistic decision not yet confirmed. */
  pending?: boolean;
}

export interface DecisionIn {
  decision: DecisionKind;
  override_label?: string;
  reason?: string;
}

export interface AuditStatus {
  version_id: string;
  state: 'none' | 'in_progress' | 'complete';
  audit_id: string | null;
  queue_id: string | null;
  size: number | null;
  decided: number;
  strata: Record<string, unknown> | null;
  result: { accept: number; override: number; flag: number; decided: number; size: number; agreement_share: number | null } | null;
}

export type QueueCreate =
  | { kind: 'label_review'; label_run_id: string; row_keys?: string[]; size?: number }
  | { kind: 'calibration_labeling'; version_id: string; question: string; label_set: string[]; size?: number; label_run_id?: string; show_model_output?: boolean };

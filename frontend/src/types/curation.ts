// Feature 004's wire types (FTDD 004 sections 4.2 and 5.1). Mirrors backend/src/services/curation/*
// result documents and backend/src/api/v1/endpoints/curation.py.

export interface ValueCounts {
  value: string;
  counts_by_label: Record<string, number>;
}

export interface ShortcutColumn {
  column: string;
  kind: 'metadata' | 'derived';
  n_rows: number;
  n_values: number;
  classes: string[];
  chance: number;
  figure: number;
  control_mean: number;
  control_runs: number;
  folds: number;
  valid: boolean;
  invalid_reason: string | null;
  per_value: { values: ValueCounts[]; other: Record<string, number> | null; other_values: number };
  bins: number[] | null;
}

export interface ExcludedColumn {
  column: string;
  reason: 'content' | 'system' | 'label' | 'label_derived';
  source_operator?: string;
}

export interface ShortcutAudit {
  label_column: string;
  label_source: string;
  classes: string[];
  class_counts: Record<string, number>;
  n_rows: number;
  unlabelled_rows: number;
  chance: number;
  columns: ShortcutColumn[];
  excluded_columns: ExcludedColumn[];
  excluded_by_band: Record<string, Record<string, number>>;
  sample_seed: number;
}

export interface LevelInForce {
  margin_pp: number;
  source: 'dataset' | 'global' | 'code_default';
  set_by: string | null;
  set_at: string | null;
  reason: string | null;
}

export interface EvaluatedWarning {
  version_id: string;
  column: string;
  figure: number;
  chance: number;
  control_mean: number;
  level: number;
  margin_pp: number;
  level_source: string;
  level_set_by: string | null;
  n_rows: number;
  sample: boolean;
  message: string;
}

export interface AuditView {
  audit: ShortcutAudit;
  warnings: EvaluatedWarning[];
  invalid: string[];
  level: LevelInForce;
}

export interface ReportOut<T = Record<string, unknown>> {
  id: string;
  kind: string;
  state: string;
  operator: string;
  params: Record<string, unknown>;
  seed: number;
  result: T | null;
  job_id: string | null;
  completed_at: string | null;
}

export type RunOutcome =
  | { outcome: 'existing' | 'inline'; report: ReportOut }
  | { outcome: 'started' | 'running'; job_id: string; report_id: string };

export interface CellSampleRow {
  row_key: string;
  occurrence: number;
  label: string;
  excerpt: Record<string, string>;
}

export interface CellSamples {
  rows: CellSampleRow[];
  seed: number;
  total: number;
  cell_rows?: number;
}

export interface LevelHistoryEntry {
  action: 'set' | 'clear';
  margin_pp: number | null;
  reason: string;
  set_by: string;
  origin: string;
  created_at: string | null;
}

export interface DatasetLevel {
  effective_margin_pp: number;
  source: string;
  level: LevelInForce;
  history: LevelHistoryEntry[];
}

export interface GlobalLevel {
  margin_pp: number;
  source: 'code_default' | 'set';
  level: LevelInForce;
  history: LevelHistoryEntry[];
}

export type Figure =
  | ({ status: 'computed'; n_rows: number; sample: boolean } & Record<string, unknown>)
  | { status: 'not_computed'; reason: string; action: string };

export interface Histogram {
  unit: string;
  edges: number[];
  counts: number[];
  median?: number;
}

export interface Profile {
  n_rows: number;
  sample: boolean;
  figures: Record<string, Figure>;
}

export interface Leakage {
  sides: string[];
  exact_pairs: Record<string, number>;
  near_pairs: Record<string, number>;
  group_pairs: Record<string, number>;
  basis: string;
  threshold: number;
  group_column: string | null;
  n_rows: number;
  pairs_total: number;
}

export interface LeakagePair {
  kind: 'exact' | 'near' | 'group';
  side_a: string;
  side_b: string;
  row_key_a: string;
  row_key_b: string;
  statistic: number;
  shared: string;
  excerpt_a: string;
  excerpt_b: string;
}

export interface BalancerPreviewCell {
  value: string;
  label: string;
  before: number;
  after: number;
}

export interface BalancerReport {
  column: string;
  label_column: string;
  cap: number;
  cells: Record<string, BalancerPreviewCell>;
  rows_in: number;
  rows_kept: number;
  rows_dropped: number;
  excluded_values: string[];
  extreme_values: Array<{
    column: string;
    value: string;
    dominant_label: string;
    dominant_rows: number;
    rows: number;
  }>;
  reaudit: ShortcutAudit | { refused: { code: string; message: string } };
}

export interface Benchmark {
  repo_id: string | null;
  revision: string | null;
  workload: string;
  source: string;
  imported: boolean;
  imported_source_id: string | null;
}

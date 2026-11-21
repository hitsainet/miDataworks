// Feature 016 (rule signals): the shapes the rule-set and validator routes answer.

export interface NextStep {
  tool: string;
  args: Record<string, unknown>;
  why: string;
}

export interface ValidatorSummary {
  id: string;
  version: number;
  ref: string;
  description: string;
  reasons: string[];
  outputs: Array<{ name: string; type: string }>;
  superseded_by: number | null;
  valid_path: string | null;
  case_count: number;
}

export interface ValidatorCase {
  id: string;
  input: Record<string, unknown>;
  expect: Record<string, unknown>;
  note?: string;
}

export interface ValidatorDetail extends ValidatorSummary {
  cases: ValidatorCase[];
}

export interface RuleSetSummary {
  name: string;
  behaviour_key: string;
  version: number | null;
  sha256: string | null;
  counts: { signals?: number; outputs?: number; vetoes?: number; cases?: number };
  last_validation: { source: string; at: string | null; passed: boolean; cases: number } | null;
  archived: boolean;
}

export interface RuleSetUsedBy {
  version_id: string;
  dataset_id: string;
  number: number;
  step_index: number;
  use: string;
}

export interface RuleSetSignal {
  id: string;
  kind: string;
  unit?: string;
  max_distance?: number;
  [key: string]: unknown;
}

export interface RuleSetDocument {
  name: string;
  behaviour_key: string;
  description: string;
  signals: RuleSetSignal[];
  expressions: Record<string, unknown>;
  outputs: Array<{ column: string; type: string; value: Record<string, unknown> }>;
  vetoes?: Array<{ id: string; when: string; removes: string[] }>;
  cases: Array<{ id: string; input: Record<string, unknown>; expect: Record<string, unknown>; note: string }>;
}

export interface RuleSetDetail {
  name: string;
  version: number;
  behaviour_key: string;
  sha256: string;
  document: RuleSetDocument;
  last_validation: RuleSetSummary['last_validation'];
  superseded_by: number | null;
  archived: boolean;
  created_at: string | null;
  used_by: RuleSetUsedBy[];
  next: NextStep;
}

export interface SignalVerdict {
  fired: boolean;
  spans: Array<[number, number]>;
  substrings: string[];
  outputs: Array<Record<string, unknown>>;
}

export interface PreviewResult {
  sha256: string;
  spans_unit: string;
  signals: Record<string, SignalVerdict>;
  vetoed: boolean;
  outputs: Record<string, unknown>;
  deciding: Record<string, Record<string, unknown>>;
}

export interface DerivationFigures {
  precision: number | null;
  recall: number | null;
  coverage: number | null;
  fired: number | null;
}

export interface RuleDerivation {
  id: string;
  job_id: string;
  source_version_id: string;
  status: string;
  state: 'measured' | 'low_coverage' | 'not_measured' | null;
  result: {
    draft: DerivationFigures;
    signals: Record<string, DerivationFigures>;
    literals: Array<{ literal: string; support: number; precision: number }>;
    held_out_per_class: { positive: number; negative: number };
    seed: number;
  } | null;
  completed_at: string | null;
}

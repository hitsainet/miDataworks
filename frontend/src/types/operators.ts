// Feature 003's wire shapes (FTDD 003 section 5.1). Mirrors backend/src/api/v1/endpoints/operators.py.

export type OperatorState = 'allowed' | 'not_allowed' | 'invalid_manifest' | 'failed_to_load' | 'duplicate';
export type OperatorKind = 'filter' | 'mapper' | 'deduplicator' | 'selector' | 'labeler' | 'generator' | 'report' | 'exporter';

export interface JsonSchemaProperty {
  type?: 'string' | 'integer' | 'number' | 'boolean' | 'array';
  enum?: Array<string | number>;
  const?: unknown;
  default?: unknown;
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  multipleOf?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  items?: JsonSchemaProperty;
  minItems?: number;
  maxItems?: number;
  uniqueItems?: boolean;
  'x-unit'?: string;
  'x-hint'?: string;
  'x-advanced'?: boolean;
  'x-widget'?: 'slider' | 'textarea' | 'select';
  [keyword: string]: unknown;
}

export interface ParamsSchema {
  type: 'object';
  properties: Record<string, JsonSchemaProperty>;
  required?: string[];
  additionalProperties: false;
}

export interface ThresholdSpec {
  param: string;
  statistic: string;
  unit: string;
  drop_when: 'below' | 'above';
  pair_param: string | null;
}

export interface Manifest {
  name: string;
  version: string;
  provider: string;
  provider_version: string;
  kind: OperatorKind;
  scope: 'row' | 'dataset';
  description: string;
  input_columns: Array<{ name: string; type: string; required: boolean }>;
  output_columns: Array<{ name: string; type: string; role: string | null }>;
  params_schema: ParamsSchema;
  thresholds: ThresholdSpec[];
  resources: { queue: string; endpoint_role: string | null; needs_lease: boolean; cpu_class: string; memory_class: string };
  deterministic: boolean;
}

export interface OperatorEntry {
  name: string;
  version: string;
  ref: string;
  state: OperatorState;
  origin: string;
  provider: string;
  error: string | null;
  manifest_hash: string | null;
  entry_point: [string, string, string] | null;
  kind: OperatorKind | null;
  description: string | null;
  provider_version: string | null;
  scope: string | null;
  has_thresholds: boolean;
  queue?: string;
  endpoint_role?: string | null;
  manifest?: Manifest;
}

export interface OperatorList {
  items: OperatorEntry[];
  total: number;
  page: number;
  limit: number;
  summary: Record<OperatorState, number>;
}

export interface SchemaSubset {
  draft: string;
  top_level: string[];
  property_types: string[];
  common: string[];
  by_type: Record<string, string[]>;
  items: string[];
  extensions: string[];
  widgets: string[];
  refused: string[];
}

export interface FieldError {
  pointer: string;
  message: string;
}

export type PreviewInput = { version_id: string; split?: string } | { step_execution_id: string };

export interface PreviewRequest {
  params: Record<string, unknown>;
  input: PreviewInput;
  sample_size?: number;
  seed?: number;
}

export interface DropExample {
  row_key: string;
  occurrence: number;
  reason_code: string;
  reason: string;
  statistic_name: string | null;
  statistic_value: number | null;
  statistic_text: string | null;
  threshold: string | null;
  excerpt?: string;
  kept_instead?: string | null;
  before?: string;
  after?: string;
}

export interface PreviewResult {
  operator: string;
  sample_size: number;
  sample_requested: number;
  seed: number;
  empty: boolean;
  message?: string;
  note?: string;
  counts: { in: number; kept: number; changed: number; dropped: number; added: number; split_assigned: number };
  examples?: {
    kept: Array<{ row_key: string; occurrence: number; excerpt: string }>;
    changed: DropExample[];
    dropped: DropExample[];
    added: DropExample[];
  };
}

export interface StatisticValue {
  row_key: string;
  occurrence: number;
  value: number | null;
  excerpt: string;
}

export interface ThresholdStatistic extends ThresholdSpec {
  current: number | null;
  constant: boolean;
  values: StatisticValue[];
}

export interface StatisticsResult {
  operator: string;
  sample_size: number;
  empty: boolean;
  thresholds: ThresholdStatistic[];
}

export type PreviewAnswer<T> = { status: 'done'; preview_id: string; result: T } | { status: 'running'; preview_id: string };

export interface AllowlistItem {
  distribution: string;
  distribution_version: string;
  entry_point: string;
  value: string;
  state: 'allowed' | 'not_allowed';
  operators: string[];
  history: Array<{ action: 'allow' | 'revoke'; reason: string; changed_by: string; created_at: string }>;
}

export interface AllowlistChange {
  distribution: string;
  distribution_version: string;
  entry_point: string;
  reason: string;
}

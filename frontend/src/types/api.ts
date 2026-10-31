// Response shapes of the Foundation routes (backend/src/schemas/*).
import type { ArchiveFields } from './archive';

export interface Dependency {
  ok: boolean;
  reason: string | null;
  configured?: boolean;
  url?: string;
  path?: string;
  /** miLLM only: its admin UI as a browser reaches it (MILLM_UI_URL), when configured. */
  ui_url?: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  /** `runner` (feature 018) is reported, never required; absent from an older backend. */
  dependencies: Record<'postgres' | 'redis' | 'data_volume' | 'millm' | 'mistudio', Dependency> & { runner?: Dependency };
  resources: {
    cpu_percent: number;
    memory_used_bytes: number;
    memory_total_bytes: number;
    disk_used_bytes: number;
    disk_total_bytes: number;
    disk_path: string;
  };
}

export type JobStatus = 'queued' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed';

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  progress: number;
  message: string | null;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  started_by: string;
  started_by_origin: 'operator' | 'agent';
  required_model_id: string | null;
  queue_reason: string | null;
  heartbeat_at: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  dismissed_at: string | null;
  created_at: string;
  room: string;
  /** Live, from a job:progress event that counts rows (label runs): rows done of the total. */
  rows_done?: number;
  rows_total?: number;
}

export interface Setting {
  key: string;
  value: string | null;
  is_sensitive: boolean;
  is_set: boolean;
  category: string;
  description: string;
  type: SettingType;
  /** Feature 012: for a runtime environment setting, where the value in force comes from. */
  source?: 'env' | 'override';
  env_value?: string | null;
  effect?: string | null;
  runtime_writable?: boolean;
}

export type SettingType = 'string' | 'secret' | 'int' | 'float' | 'bool' | 'address' | 'choice';

/** One environment setting on the Settings screen (feature 012, GET /api/v1/settings/environment). */
export interface EnvironmentRow {
  key: string;
  env_var: string;
  label: string;
  description: string;
  group: 'runtime' | 'restart' | 'approval_rule' | 'environment';
  type: SettingType;
  value: unknown;
  env_value: unknown;
  source: 'env' | 'override';
  runtime_writable: boolean;
  agent_writable: boolean;
  effect: string | null;
  effect_detail: string | null;
  restart: string | null;
  caution: string | null;
  secret: boolean;
  is_set: boolean;
  minimum: number | null;
  maximum: number | null;
  exclusive_minimum: number | null;
  exclusive_maximum: number | null;
  choices: string[] | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface SettingChange {
  key: string;
  previous_value: string | null;
  new_value: string | null;
  actor: string;
  origin: string;
  approval_id: string | null;
  reason: string | null;
  changed_at: string;
}

export interface EnvironmentListing {
  settings: EnvironmentRow[];
  history: SettingChange[];
}

export type Role = 'classifier' | 'judge' | 'generation' | 'embeddings';

export interface EndpointRole {
  role: Role;
  configured: boolean;
  protocol: string | null;
  base_url: string | null;
  model_id: string | null;
  api_key: string | null;
  has_api_key: boolean;
  /** scheme://host:port the stored key was entered for; the key is sent to no other server. */
  key_origin?: string | null;
  inherit_from_judge: boolean;
  use_mode: 'own' | 'same_as_classifier' | 'none';
  effective_role: Role | null;
  /** Every key stored for this role, one per server; the one for the current URL is in use (014). */
  stored_keys?: StoredKey[];
  /** On a save's answer: the endpoint moved back to a server whose key was kept; it is in use again. */
  key_restored?: boolean;
  /** On a save's answer: the endpoint moved away; the key in use was kept for its own server. */
  key_parked?: boolean;
  key_notice?: string | null;
  /** 017: US dollars per call (one row, one question); null when not entered. */
  price_per_call_usd?: number | null;
  /** 017: runs on this role count as paid (a price above zero, or a hosted protocol unpriced). */
  paid?: boolean;
}

export interface StoredKey {
  /** scheme://host:port: the only server this key is sent to. */
  origin: string;
  /** Masked. */
  api_key: string;
  in_use: boolean;
}

/** Feature 014: the Settings PIN. Not a login. */
export interface PinStatus {
  configured: boolean;
  bypass_active: boolean;
  session_valid: boolean;
  session_ttl_s: number;
  header: string;
}

export interface PinSession {
  token: string;
  expires_at: string;
  header: string;
}

export interface EndpointRoleWrite {
  protocol: string | null;
  base_url: string | null;
  model_id: string | null;
  api_key: string | null;
  inherit_from_judge: boolean;
  use_mode: 'own' | 'same_as_classifier' | 'none';
  /** 017: decimal text, up to six decimals; null keeps the stored price. */
  price_per_call_usd?: string | null;
  /** 017: removes the stored price. */
  clear_price?: boolean;
}

export interface ModelList {
  role: Role;
  models: string[];
  source: 'openai' | 'tei' | 'typesafe';
  url: string;
}

export interface Approval extends ArchiveFields {
  id: string;
  action: string;
  target: string;
  summary: string;
  payload: Record<string, unknown>;
  request_digest: string;
  requested_by: string;
  status: 'pending' | 'executing' | 'executed' | 'failed' | 'rejected' | 'expired';
  expires_at: string;
  decided_by: string | null;
  decided_at?: string | null;
  reason?: string | null;
  result_kind?: string | null;
  result_id?: string | null;
  error?: Record<string, unknown> | null;
  created_at: string;
  /** ENH-006 D79: set when the operator approved this request with others in one decision. */
  batch_id?: string | null;
  /** ENH-006 D79: a Settings change (its route is behind the Settings PIN); the server decides. */
  settings_change?: boolean;
}

/** ENH-006 D79/D81: what one batch approves; settings changes and code rules are never mixed. */
export type BatchKind = 'settings' | 'code_rules';

/** ENH-006 D79/D81: the answer to "Approve all settings changes" or "Approve all code rules". */
export interface ApproveBatchResult {
  batch_id: string;
  kind?: BatchKind;
  results: {
    approval_id: string;
    status: string;
    result_kind?: string | null;
    result_id?: string | null;
    error?: { code?: string; message?: string } | null;
  }[];
}

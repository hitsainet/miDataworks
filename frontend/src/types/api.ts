// Response shapes of the Foundation routes (backend/src/schemas/*).

export interface Dependency {
  ok: boolean;
  reason: string | null;
  configured?: boolean;
  url?: string;
  path?: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  dependencies: Record<'postgres' | 'redis' | 'data_volume' | 'millm' | 'mistudio', Dependency>;
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
}

export interface Setting {
  key: string;
  value: string | null;
  is_sensitive: boolean;
  is_set: boolean;
  category: string;
  description: string;
  type: 'string' | 'secret' | 'bool';
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
  inherit_from_judge: boolean;
  use_mode: 'own' | 'same_as_classifier' | 'none';
  effective_role: Role | null;
}

export interface EndpointRoleWrite {
  protocol: string | null;
  base_url: string | null;
  model_id: string | null;
  api_key: string | null;
  inherit_from_judge: boolean;
  use_mode: 'own' | 'same_as_classifier' | 'none';
}

export interface ModelList {
  role: Role;
  models: string[];
  source: 'openai' | 'tei';
  url: string;
}

export interface Approval {
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
  created_at: string;
}

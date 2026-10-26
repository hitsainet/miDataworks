// Typed calls to the Foundation routes. Stores call these; components call stores.
import type { Approval, EndpointRole, EndpointRoleWrite, EnvironmentListing, Health, Job, ModelList, PinSession, PinStatus, Role, Setting } from '@/types/api';

import { api, json } from './client';

export const healthApi = {
  get: () => api<Health>('/api/health'),
};

export const jobsApi = {
  list: (state: 'active' | 'failed') => api<{ jobs: Job[] }>(`/api/v1/jobs?state=${state}`),
  get: (id: string) => api<Job>(`/api/v1/jobs/${encodeURIComponent(id)}`),
  cancel: (id: string) => api<{ job: Job; detail: string }>(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, json('POST', {})),
  dismiss: (id: string) => api<Job>(`/api/v1/jobs/${encodeURIComponent(id)}/dismiss`, json('POST')),
  startSelftest: (durationSeconds: number) =>
    api<Job>('/api/v1/jobs/selftest', json('POST', { duration_seconds: durationSeconds })),
};

export const settingsApi = {
  list: () => api<Setting[]>('/api/v1/settings'),
  put: (key: string, value: string, reason?: string) =>
    api<Setting>(`/api/v1/settings/${encodeURIComponent(key)}`, json('PUT', reason ? { value, reason } : { value })),
  remove: (key: string, reason?: string) =>
    api<void>(
      `/api/v1/settings/${encodeURIComponent(key)}` + (reason ? `?reason=${encodeURIComponent(reason)}` : ''),
      json('DELETE'),
    ),
  /** Feature 012: every environment setting, grouped, with the last 50 changes. */
  environment: () => api<EnvironmentListing>('/api/v1/settings/environment'),
};

/** Feature 014: the Settings PIN. Unlocking answers a session the client then sends itself. */
export const pinApi = {
  status: () => api<PinStatus>('/api/v1/settings/pin'),
  unlock: (pin: string) => api<PinSession>('/api/v1/settings/pin/session', json('POST', { pin })),
  set: (pin: string, currentPin?: string) =>
    api<PinSession>('/api/v1/settings/pin', json('PUT', currentPin ? { pin, current_pin: currentPin } : { pin })),
  remove: (currentPin: string) => api<PinStatus>('/api/v1/settings/pin/remove', json('POST', { current_pin: currentPin })),
};

export const rolesApi = {
  list: () => api<EndpointRole[]>('/api/v1/endpoint-roles'),
  put: (role: Role, body: EndpointRoleWrite) => api<EndpointRole>(`/api/v1/endpoint-roles/${role}`, json('PUT', body)),
  /** Feature 014: delete the key stored for one server (in use or kept). */
  deleteKey: (role: Role, origin: string) =>
    api<EndpointRole>(`/api/v1/endpoint-roles/${role}/keys?origin=${encodeURIComponent(origin)}`, json('DELETE')),
  fetchModels: (role: Role, baseUrl?: string) =>
    api<ModelList>(
      `/api/v1/endpoint-roles/${role}/models` + (baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : ''),
    ),
};

export const approvalsApi = {
  pending: () => api<{ approvals: Approval[] }>('/api/v1/approvals?status=pending'),
  approve: (id: string) => api<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/approve`, json('POST')),
  reject: (id: string, reason: string) =>
    api<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/reject`, json('POST', { reason })),
  /** Every approval, newest first; archived ones only when asked for. */
  all: (includeArchived = false) =>
    api<{ approvals: Approval[] }>(`/api/v1/approvals${includeArchived ? '?include_archived=true' : ''}`),
  archive: (id: string) => api<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/archive`, json('POST', {})),
  unarchive: (id: string) => api<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/unarchive`, json('POST', {})),
};

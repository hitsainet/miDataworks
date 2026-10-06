// Typed calls to the Foundation routes. Stores call these; components call stores.
import type { Approval, EndpointRole, EndpointRoleWrite, Health, Job, ModelList, Role, Setting } from '@/types/api';

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
  put: (key: string, value: string) => api<Setting>(`/api/v1/settings/${encodeURIComponent(key)}`, json('PUT', { value })),
  remove: (key: string) => api<void>(`/api/v1/settings/${encodeURIComponent(key)}`, json('DELETE')),
};

export const rolesApi = {
  list: () => api<EndpointRole[]>('/api/v1/endpoint-roles'),
  put: (role: Role, body: EndpointRoleWrite) => api<EndpointRole>(`/api/v1/endpoint-roles/${role}`, json('PUT', body)),
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
};

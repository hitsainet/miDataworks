// Feature 009 routes (FTDD 009 section 5.1).
import { api, json } from '@/api/client';
import type {
  ChecksResult,
  DetectorSet,
  DetectorSetSummary,
  ResultsSnapshot,
  RoleIn,
  SendDetail,
  SendStarted,
  SendSummary,
} from '@/types/detectorSets';

const enc = encodeURIComponent;

export const detectorSetsApi = {
  list: () => api<{ items: DetectorSetSummary[]; total: number }>('/api/v1/detector-sets?limit=200'),
  get: (id: string) => api<DetectorSet>(`/api/v1/detector-sets/${enc(id)}`),
  create: (body: { name: string; description?: string; positive_meaning?: string; roles: RoleIn[]; monitored_ref?: unknown }) =>
    api<DetectorSet>('/api/v1/detector-sets', json('POST', body)),
  update: (id: string, body: Record<string, unknown>) => api<DetectorSet>(`/api/v1/detector-sets/${enc(id)}`, json('PATCH', body)),
  archive: (id: string) => api<DetectorSet>(`/api/v1/detector-sets/${enc(id)}/archive`, json('POST')),
  checks: (id: string) => api<ChecksResult>(`/api/v1/detector-sets/${enc(id)}/checks`, json('POST')),
  send: (id: string, body: { namespace?: string | null; repositories?: Record<string, string>; visibility: 'private' | 'public' }) =>
    api<SendStarted>(`/api/v1/detector-sets/${enc(id)}/send`, json('POST', body)),
  sends: (id: string) => api<{ items: SendSummary[] }>(`/api/v1/detector-sets/${enc(id)}/sends`),
  sendDetail: (sendId: string) => api<SendDetail>(`/api/v1/detector-sends/${enc(sendId)}`),
  resume: (sendId: string) => api<SendStarted>(`/api/v1/detector-sends/${enc(sendId)}/resume`, json('POST')),
  cancel: (sendId: string) => api<{ status: string }>(`/api/v1/detector-sends/${enc(sendId)}/cancel`, json('POST')),
  refresh: (id: string) => api<ResultsSnapshot>(`/api/v1/detector-sets/${enc(id)}/results/refresh`, json('POST')),
  results: (id: string) => api<ResultsSnapshot>(`/api/v1/detector-sets/${enc(id)}/results`),
  markReward: (probeId: string, reason: string) =>
    api<Record<string, unknown>>('/api/v1/reward-marks', json('POST', { mistudio_probe_id: probeId, reason })),
};

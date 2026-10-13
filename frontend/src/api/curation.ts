// Feature 004's routes (FTDD 004 section 5.1). Run routes answer 200 (stored or inline) or 202 (a job).
import { api, json } from '@/api/client';
import type {
  AuditView,
  Benchmark,
  CellSamples,
  DatasetLevel,
  GlobalLevel,
  LeakagePair,
  ReportOut,
  RunOutcome,
} from '@/types/curation';

const enc = encodeURIComponent;
const v = (id: string) => `/api/v1/versions/${enc(id)}`;

export const curationApi = {
  runAudit: (id: string, body: { label_column?: string } = {}) => api<RunOutcome>(`${v(id)}/shortcut-audit`, json('POST', body)),
  audit: (id: string) => api<AuditView>(`${v(id)}/shortcut-audit`),
  cells: (id: string, q: { column: string; value: string; label: string; page?: number; limit?: number }) =>
    api<CellSamples>(
      `${v(id)}/shortcut-audit/cells?${new URLSearchParams({
        column: q.column,
        value: q.value,
        label: q.label,
        page: String(q.page ?? 0),
        limit: String(q.limit ?? 30),
      }).toString()}`,
    ),
  runProfile: (id: string, body: { sample_size?: number } = {}) => api<RunOutcome>(`${v(id)}/profile`, json('POST', body)),
  profile: (id: string) => api<ReportOut>(`${v(id)}/profile`),
  runLeakage: (id: string, body: { group_column?: string; threshold?: number } = {}) =>
    api<RunOutcome>(`${v(id)}/leakage`, json('POST', body)),
  leakage: (id: string) => api<ReportOut>(`${v(id)}/leakage`),
  leakagePairs: (id: string, page = 0, limit = 50) =>
    api<{ pairs: LeakagePair[]; total: number }>(`${v(id)}/leakage/pairs?page=${page}&limit=${limit}`),
  contamination: (id: string) => api<ReportOut>(`${v(id)}/contamination`),
  benchmarks: () => api<{ items: Benchmark[] }>('/api/v1/curation/benchmarks'),
  datasetLevel: (datasetId: string) => api<DatasetLevel>(`/api/v1/datasets/${enc(datasetId)}/shortcut-level`),
  setDatasetLevel: (datasetId: string, marginPp: number, reason: string) =>
    api<DatasetLevel>(`/api/v1/datasets/${enc(datasetId)}/shortcut-level`, json('PUT', { margin_pp: marginPp, reason })),
  clearDatasetLevel: (datasetId: string, reason: string) =>
    api<DatasetLevel>(`/api/v1/datasets/${enc(datasetId)}/shortcut-level`, json('DELETE', { reason })),
  globalLevel: () => api<GlobalLevel>('/api/v1/settings/shortcut-level'),
  setGlobalLevel: (marginPp: number, reason: string) =>
    api<GlobalLevel>('/api/v1/settings/shortcut-level', json('PUT', { margin_pp: marginPp, reason })),
};

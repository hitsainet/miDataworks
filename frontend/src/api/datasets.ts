// Feature 002's dataset and version calls. Stores call these; components call stores.
import type {
  BuildAccepted,
  BuildRequest,
  CompareReport,
  Dataset,
  DatasetSummary,
  DatasetsMeta,
  Lineage,
  RowHistory,
  RowPage,
  Version,
  VersionSummary,
} from '@/types/versions';

import { api, json } from './client';

const q = (params: Record<string, string | number | undefined | null>) => {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : '';
};

export const datasetsApi = {
  meta: () => api<DatasetsMeta>('/api/v1/datasets/meta'),
  list: (params: { q?: string; target_type?: string } = {}) =>
    api<{ items: DatasetSummary[]; total: number }>(`/api/v1/datasets${q({ ...params, limit: 200 })}`),
  get: (id: string) => api<Dataset>(`/api/v1/datasets/${encodeURIComponent(id)}`),
  create: (body: { name: string; target_type: string; description?: string }) =>
    api<Dataset>('/api/v1/datasets', json('POST', body)),
  patch: (id: string, body: { target_type?: string; description?: string }) =>
    api<Dataset>(`/api/v1/datasets/${encodeURIComponent(id)}`, json('PATCH', body)),
};

const v = (id: string) => `/api/v1/versions/${encodeURIComponent(id)}`;

export const versionsApi = {
  list: (datasetId?: string) => api<{ items: VersionSummary[]; total: number }>(`/api/v1/versions${q({ dataset_id: datasetId, limit: 200 })}`),
  get: (id: string) => api<Version>(v(id)),
  build: (body: BuildRequest & { recipe_revision_id: string }) => api<Version | BuildAccepted>('/api/v1/versions', json('POST', body)),
  rows: (id: string, params: { split?: string; q?: string; page?: number; limit?: number } = {}) =>
    api<RowPage>(`${v(id)}/rows${q(params)}`),
  history: (id: string, params: { row_key?: string; q?: string }) =>
    api<{ query: string | null; results: RowHistory[] }>(`${v(id)}/rows/history${q(params)}`),
  lineage: (id: string) => api<Lineage>(`${v(id)}/lineage`),
  compare: (id: string, other?: string) => api<CompareReport>(`${v(id)}/compare${q({ with: other })}`),
  verify: (id: string) => api<{ job_id: string }>(`${v(id)}/verify-rebuild`, json('POST')),
  remove: (id: string, reason: string) => api<Version>(v(id), json('DELETE', { reason })),
};

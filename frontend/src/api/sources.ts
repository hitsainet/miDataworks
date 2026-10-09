// Feature 001's client. Every call goes to /api/v1/sources/*: the browser never calls Hugging Face
// (001 FTDD section 8; FTASKS 13.3). The access token is a call argument only: it is sent once and
// never kept here or in a store.
import { api, json } from './client';
import type {
  AnnotationRequest,
  ApprovalPending,
  HfPreview,
  HfRequest,
  ImportAccepted,
  SourceAnnotation,
  SourceDetail,
  SourcesMeta,
  SourceSummary,
  UploadManifest,
} from '@/types/sources';

const BASE = '/api/v1/sources';
const id = (sourceId: string) => `${BASE}/${encodeURIComponent(sourceId)}`;

function withToken(request: HfRequest, token?: string): Record<string, unknown> {
  const body: Record<string, unknown> = { repo_id: request.repo_id.trim() };
  for (const key of ['config', 'split', 'revision'] as const) {
    const value = request[key]?.trim();
    if (value) body[key] = value;
  }
  if (token && token.trim()) body.access_token = token.trim();
  return body;
}

export const sourcesApi = {
  meta: () => api<SourcesMeta>(`${BASE}/meta`),
  list: (params: { state?: string; page?: number; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.state) q.set('state', params.state);
    q.set('page', String(params.page ?? 1));
    q.set('limit', String(params.limit ?? 50));
    return api<{ items: SourceSummary[]; total: number; page: number; limit: number }>(`${BASE}?${q}`);
  },
  get: (sourceId: string) => api<SourceDetail>(id(sourceId)),
  preview: (request: HfRequest, token?: string) => api<HfPreview>(`${BASE}/hf/preview`, json('POST', withToken(request, token))),
  importHf: (request: HfRequest & { confirm_large?: boolean }, token?: string) =>
    api<SourceDetail | ImportAccepted | ApprovalPending>(`${BASE}/hf`, json('POST', { ...withToken(request, token), ...(request.confirm_large ? { confirm_large: true } : {}) })),
  upload: (files: File[], manifest: UploadManifest) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('manifest', JSON.stringify(manifest));
    return api<SourceDetail | ImportAccepted>(`${BASE}/uploads`, { method: 'POST', body: form });
  },
  annotate: (sourceId: string, body: AnnotationRequest) => api<SourceAnnotation>(`${id(sourceId)}/annotations`, json('POST', body)),
  remove: (sourceId: string, reason: string) => api<SourceDetail>(id(sourceId), json('DELETE', { reason })),
};

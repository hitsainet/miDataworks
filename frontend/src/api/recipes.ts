// Origin: miStudio (Onegaishimas/miStudio) frontend/src/api/trainingTemplates.ts @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: one typed function per route, list/get/create/export/import.
// Changed: revisions, clone and archive replace update and delete; export returns the response
// BLOB unchanged — never res.json() then JSON.stringify — so the recipe hash inside describes the
// file (FR-002.15); import uploads the file as multipart.
import type { BuildAccepted, BuildRequest, Version } from '@/types/versions';
import type { ImportResult, Recipe, RecipeBody, RecipeRevision, RecipeSummary, Validation } from '@/types/recipes';

import { ApiError, api, json } from './client';

const r = (id: string) => `/api/v1/recipes/${encodeURIComponent(id)}`;

export const recipesApi = {
  list: (archived = false) => api<{ items: RecipeSummary[]; total: number }>(`/api/v1/recipes?limit=200&archived=${archived}`),
  get: (id: string) => api<Recipe>(r(id)),
  create: (body: { name: string; description?: string; body: RecipeBody; step_labels?: string[] }) =>
    api<Recipe>('/api/v1/recipes', json('POST', body)),
  revise: (id: string, body: { body: RecipeBody; step_labels?: string[] }) =>
    api<RecipeRevision>(`${r(id)}/revisions`, json('POST', body)),
  clone: (id: string, name: string, revisionId?: string) =>
    api<Recipe>(`${r(id)}/clone`, json('POST', { name, revision_id: revisionId })),
  archive: (id: string) => api<Recipe>(`${r(id)}/archive`, json('POST')),
  validate: (body: unknown) => api<Validation>('/api/v1/recipes/validate', json('POST', { body })),
  build: (id: string, body: BuildRequest) => api<Version | BuildAccepted>(`${r(id)}/build`, json('POST', body)),
  /** The export file exactly as the backend wrote it. */
  exportBlob: async (id: string, revisionId: string): Promise<Blob> => {
    const res = await fetch(`${r(id)}/revisions/${encodeURIComponent(revisionId)}/export`);
    if (!res.ok) throw new ApiError(res.status, 'EXPORT_FAILED', `The export answered ${res.status}.`);
    return res.blob();
  },
  importFile: async (file: File): Promise<ImportResult> => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/v1/recipes/import', { method: 'POST', body: form });
    const body = await res.json().catch(() => undefined);
    if (!res.ok) {
      const error = (body as { error?: { code?: string; message?: string } } | undefined)?.error;
      throw new ApiError(res.status, error?.code ?? 'HTTP_ERROR', error?.message ?? `The import answered ${res.status}.`);
    }
    return body as ImportResult;
  },
};

// Recipe drafts (FR-002.18, FR-002.48): saved as the user goes; may be invalid.
import type { DraftWrite, RecipeDraft, RecipeRevision } from '@/types/recipes';

import { api, json } from './client';

const d = (id: string) => `/api/v1/recipe-drafts/${encodeURIComponent(id)}`;

export const draftsApi = {
  list: () => api<{ items: RecipeDraft[]; total: number }>('/api/v1/recipe-drafts'),
  get: (id: string) => api<RecipeDraft>(d(id)),
  create: (body: Partial<DraftWrite>) => api<RecipeDraft>('/api/v1/recipe-drafts', json('POST', body)),
  update: (id: string, body: Partial<DraftWrite>) => api<RecipeDraft>(d(id), json('PUT', body)),
  remove: (id: string) => api<void>(d(id), json('DELETE')),
  save: (id: string, recipeName?: string) =>
    api<RecipeRevision>(`${d(id)}/save`, json('POST', recipeName ? { recipe_name: recipeName } : {})),
};

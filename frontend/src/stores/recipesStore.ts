// Origin: miStudio (Onegaishimas/miStudio) frontend/src/stores/trainingTemplatesStore.ts @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: one Zustand store, CRUD actions that refetch rather than
// patch, an error string the panel shows. Changed: no optimistic writes (a recipe is hashed by the
// backend; the client never guesses the hash), revisions/clone/archive replace update/delete,
// export downloads the response blob unchanged, favourites dropped.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { recipesApi } from '@/api/recipes';
import type { ImportResult, Recipe, RecipeBody, RecipeSummary, Validation } from '@/types/recipes';
import type { BuildAccepted, BuildRequest, Version } from '@/types/versions';

interface RecipesState {
  recipes: RecipeSummary[];
  showArchived: boolean;
  selected: Recipe | null;
  validation: Validation | null;
  importResult: ImportResult | null;
  loading: boolean;
  error: string | null;
  notice: string | null;
  fetchRecipes: () => Promise<void>;
  setShowArchived: (value: boolean) => Promise<void>;
  fetchRecipe: (id: string) => Promise<Recipe | null>;
  validateBody: (body: RecipeBody) => Promise<Validation | null>;
  createRecipe: (name: string, body: RecipeBody, labels?: string[], description?: string) => Promise<Recipe | null>;
  reviseRecipe: (id: string, body: RecipeBody, labels?: string[]) => Promise<boolean>;
  cloneRecipe: (id: string, name: string, revisionId?: string) => Promise<Recipe | null>;
  archiveRecipe: (id: string) => Promise<void>;
  exportRecipe: (id: string, revisionId: string, name: string) => Promise<void>;
  importRecipe: (file: File) => Promise<ImportResult | null>;
  buildFromRecipe: (id: string, request: BuildRequest) => Promise<Version | BuildAccepted | null>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');
const detailsOf = (e: unknown): Validation | null =>
  e instanceof ApiError && e.code === 'recipe_invalid' ? (e.details as unknown as Validation) : null;

/** Save a Blob as a file, byte for byte (the backend wrote it; the browser never re-serialises). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export const useRecipesStore = create<RecipesState>()((set, get) => ({
  recipes: [],
  showArchived: false,
  selected: null,
  validation: null,
  importResult: null,
  loading: false,
  error: null,
  notice: null,
  fetchRecipes: async () => {
    set({ loading: true });
    try {
      const { items } = await recipesApi.list(get().showArchived);
      set({ recipes: items, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: message(e) });
    }
  },
  setShowArchived: async (value) => {
    set({ showArchived: value });
    await get().fetchRecipes();
  },
  fetchRecipe: async (id) => {
    try {
      const recipe = await recipesApi.get(id);
      set({ selected: recipe });
      return recipe;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  validateBody: async (body) => {
    try {
      const validation = await recipesApi.validate(body);
      set({ validation });
      return validation;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  createRecipe: async (name, body, labels, description) => {
    set({ error: null, validation: null });
    try {
      const recipe = await recipesApi.create({ name, body, step_labels: labels, description });
      set({ notice: `Saved recipe ${recipe.name}.` });
      await get().fetchRecipes();
      return recipe;
    } catch (e) {
      set({ error: message(e), validation: detailsOf(e) });
      return null;
    }
  },
  reviseRecipe: async (id, body, labels) => {
    set({ error: null, validation: null });
    try {
      const revision = await recipesApi.revise(id, { body, step_labels: labels });
      set({ notice: `Saved revision ${revision.revision_number}.` });
      await get().fetchRecipes();
      return true;
    } catch (e) {
      set({ error: message(e), validation: detailsOf(e) });
      return false;
    }
  },
  cloneRecipe: async (id, name, revisionId) => {
    set({ error: null });
    try {
      const recipe = await recipesApi.clone(id, name, revisionId);
      set({ notice: `Cloned into ${recipe.name}.` });
      await get().fetchRecipes();
      return recipe;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  archiveRecipe: async (id) => {
    set({ error: null });
    try {
      const recipe = await recipesApi.archive(id);
      set({ notice: `Archived ${recipe.name}. Versions built from it still resolve.` });
    } catch (e) {
      set({ error: message(e) });
    }
    await get().fetchRecipes();
  },
  exportRecipe: async (id, revisionId, name) => {
    try {
      downloadBlob(await recipesApi.exportBlob(id, revisionId), `${name}.recipe.json`);
    } catch (e) {
      set({ error: message(e) });
    }
  },
  importRecipe: async (file) => {
    set({ error: null, importResult: null });
    try {
      const result = await recipesApi.importFile(file);
      set({ importResult: result });
      await get().fetchRecipes();
      return result;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  buildFromRecipe: async (id, request) => {
    set({ error: null });
    try {
      return await recipesApi.build(id, request);
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
}));

// Datasets and feature 002's metadata (/datasets/meta is the one source of target types, defaults
// and guided steps: no frontend list can drift from the backend's enums).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { datasetsApi } from '@/api/datasets';
import type { Dataset, DatasetSummary, DatasetsMeta } from '@/types/versions';

interface DatasetsState {
  meta: DatasetsMeta | null;
  datasets: DatasetSummary[];
  loading: boolean;
  error: string | null;
  fetchMeta: () => Promise<DatasetsMeta | null>;
  /** includeArchived: the Datasets screen's "Show archived" toggle. Pickers call it bare. */
  fetchDatasets: (includeArchived?: boolean) => Promise<void>;
  archiveDataset: (id: string, includeArchived: boolean) => Promise<void>;
  unarchiveDataset: (id: string, includeArchived: boolean) => Promise<void>;
  createDataset: (name: string, targetType: string, description?: string) => Promise<Dataset | null>;
  setTargetType: (id: string, targetType: string) => Promise<Dataset | null>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');

export const useDatasetsStore = create<DatasetsState>()((set, get) => ({
  meta: null,
  datasets: [],
  loading: false,
  error: null,
  fetchMeta: async () => {
    if (get().meta) return get().meta;
    try {
      const meta = await datasetsApi.meta();
      set({ meta });
      return meta;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  fetchDatasets: async (includeArchived = false) => {
    set({ loading: true });
    try {
      const { items } = await datasetsApi.list({ include_archived: includeArchived });
      set({ datasets: items, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: message(e) });
    }
  },
  archiveDataset: async (id, includeArchived) => {
    set({ error: null });
    try {
      await datasetsApi.archive(id);
    } catch (e) {
      set({ error: message(e) });
      return;
    }
    await get().fetchDatasets(includeArchived);
  },
  unarchiveDataset: async (id, includeArchived) => {
    set({ error: null });
    try {
      await datasetsApi.unarchive(id);
    } catch (e) {
      set({ error: message(e) });
      return;
    }
    await get().fetchDatasets(includeArchived);
  },
  createDataset: async (name, targetType, description) => {
    set({ error: null });
    try {
      const created = await datasetsApi.create({ name, target_type: targetType, description });
      await get().fetchDatasets();
      return created;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  setTargetType: async (id, targetType) => {
    set({ error: null });
    try {
      return await datasetsApi.patch(id, { target_type: targetType });
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
}));

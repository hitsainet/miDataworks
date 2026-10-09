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
  fetchDatasets: () => Promise<void>;
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
  fetchDatasets: async () => {
    set({ loading: true });
    try {
      const { items } = await datasetsApi.list();
      set({ datasets: items, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: message(e) });
    }
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

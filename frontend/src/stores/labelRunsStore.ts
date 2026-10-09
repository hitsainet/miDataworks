// Label runs (FTDD 005 section 7.2): the list, runs by id, and the events of a followed run. The
// socket subscription and the polling fallback live in hooks/useLabelRun.ts.
import { create } from 'zustand';

import { labelingApi } from '@/api/labeling';
import { ApiError } from '@/api/client';
import type { Label, LabelRun } from '@/types/labeling';

export interface LiveProgress {
  rows_done: number;
  rows_total: number;
  rows_per_second: number | null;
  eta_seconds: number | null;
  counts: Record<string, number>;
}

interface LabelRunsState {
  list: LabelRun[];
  byId: Record<string, LabelRun>;
  labels: Record<string, Label[]>;
  live: Record<string, LiveProgress>;
  selectedId: string | null;
  error: string | null;
  fetchRuns: () => Promise<void>;
  fetchRun: (id: string) => Promise<LabelRun | null>;
  fetchLabels: (id: string) => Promise<void>;
  selectRun: (id: string | null) => void;
  cancelRun: (id: string) => Promise<void>;
  resumeRun: (id: string) => Promise<void>;
  rederiveRun: (id: string, positive: number, negative: number) => Promise<LabelRun | null>;
  applyEvent: (runId: string, event: string, data: Record<string, unknown>) => void;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export const useLabelRunsStore = create<LabelRunsState>((set, get) => ({
  list: [],
  byId: {},
  labels: {},
  live: {},
  selectedId: null,
  error: null,
  fetchRuns: async () => {
    try {
      const page = await labelingApi.list();
      set((s) => ({ list: page.items, byId: { ...s.byId, ...Object.fromEntries(page.items.map((r) => [r.id, r])) }, error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchRun: async (id) => {
    try {
      const run = await labelingApi.get(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)) }));
      return run;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  fetchLabels: async (id) => {
    try {
      const page = await labelingApi.labels(id);
      set((s) => ({ labels: { ...s.labels, [id]: page.items } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  selectRun: (id) => set({ selectedId: id }),
  cancelRun: async (id) => {
    try {
      const run = await labelingApi.cancel(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)), error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  resumeRun: async (id) => {
    try {
      const run = await labelingApi.resume(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)), error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  rederiveRun: async (id, positive, negative) => {
    try {
      const child = await labelingApi.rederive(id, { threshold_positive: positive, threshold_negative: negative });
      set((s) => ({ list: [child, ...s.list], byId: { ...s.byId, [child.id]: child }, selectedId: child.id, error: null }));
      return child;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  applyEvent: (runId, event, data) => {
    if (event === 'label_run:progress') {
      set((s) => ({ live: { ...s.live, [runId]: data as unknown as LiveProgress } }));
      return;
    }
    // completed / failed / cancelled: the run record is the truth; read it.
    void get().fetchRun(runId);
  },
}));

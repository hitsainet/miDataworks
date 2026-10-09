// Versions: detail, rows, row history, lineage, comparisons, and live build progress.
// Versions and comparisons are immutable, so they are cached forever (FTDD 002 section 7.3);
// head and supersession are always refetched with the version.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { versionsApi } from '@/api/datasets';
import { jobsApi } from '@/api/endpoints';
import { safeStorage } from '@/stores/safeStorage';
import type { CompareReport, Lineage, RowHistory, RowPage, Version, VersionSummary } from '@/types/versions';

export const SELECTED_VERSION_KEY = 'midataworks-selected-version';

export interface BuildProgress {
  jobId: string;
  status: string;
  phase: string | null;
  percent: number;
  versionId: string | null;
  error: string | null;
}

interface VersionsState {
  selectedId: string | null;
  versionList: VersionSummary[];
  versions: Record<string, Version>;
  rows: RowPage | null;
  history: RowHistory[] | null;
  lineage: Lineage | null;
  compares: Record<string, CompareReport>;
  build: BuildProgress | null;
  loading: boolean;
  error: string | null;
  selectVersion: (id: string | null) => void;
  fetchVersionList: () => Promise<void>;
  fetchVersion: (id: string) => Promise<Version | null>;
  fetchRows: (id: string, opts?: { split?: string; q?: string; page?: number }) => Promise<void>;
  findRowHistory: (id: string, query: { row_key?: string; q?: string }) => Promise<void>;
  clearHistory: () => void;
  fetchLineage: (id: string) => Promise<void>;
  fetchCompare: (id: string, other?: string) => Promise<CompareReport | null>;
  trackBuild: (jobId: string) => void;
  applyBuildEvent: (event: string, data: Record<string, unknown>) => void;
  pollBuild: () => Promise<void>;
  verifyRebuild: (id: string) => Promise<string | null>;
  deleteVersion: (id: string, reason: string) => Promise<boolean>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');

function storedSelection(): string | null {
  const value = safeStorage.getItem(SELECTED_VERSION_KEY);
  return typeof value === 'string' ? value : null;
}

export const useVersionsStore = create<VersionsState>()((set, get) => ({
  selectedId: storedSelection(),
  versionList: [],
  versions: {},
  rows: null,
  history: null,
  lineage: null,
  compares: {},
  build: null,
  loading: false,
  error: null,
  selectVersion: (id) => {
    set({ selectedId: id, rows: null, history: null, lineage: null });
    if (id) void safeStorage.setItem(SELECTED_VERSION_KEY, id);
    else safeStorage.removeItem(SELECTED_VERSION_KEY);
  },
  fetchVersionList: async () => {
    try {
      const { items } = await versionsApi.list();
      set({ versionList: items });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchVersion: async (id) => {
    set({ loading: true, error: null });
    try {
      const version = await versionsApi.get(id);
      set((s) => ({ versions: { ...s.versions, [id]: version }, loading: false }));
      return version;
    } catch (e) {
      set({ loading: false, error: message(e) });
      return null;
    }
  },
  fetchRows: async (id, opts = {}) => {
    try {
      set({ rows: await versionsApi.rows(id, { ...opts, limit: 50 }) });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  findRowHistory: async (id, query) => {
    set({ error: null });
    try {
      const { results } = await versionsApi.history(id, query);
      set({ history: results });
    } catch (e) {
      set({ error: message(e), history: [] });
    }
  },
  clearHistory: () => set({ history: null }),
  fetchLineage: async (id) => {
    try {
      set({ lineage: await versionsApi.lineage(id) });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchCompare: async (id, other) => {
    const key = `${id}:${other ?? 'parent'}`;
    const cached = get().compares[key];
    if (cached) return cached;
    try {
      const report = await versionsApi.compare(id, other);
      set((s) => ({ compares: { ...s.compares, [key]: report } }));
      return report;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  trackBuild: (jobId) =>
    set({ build: { jobId, status: 'queued', phase: null, percent: 0, versionId: null, error: null } }),
  applyBuildEvent: (event, data) => {
    const build = get().build;
    if (!build || data.job_id !== build.jobId) return;
    if (event.endsWith(':progress')) {
      set({
        build: {
          ...build,
          status: 'running',
          phase: typeof data.phase === 'string' ? data.phase : build.phase,
          percent: typeof data.percent === 'number' ? data.percent : build.percent,
        },
      });
    } else if (event.endsWith(':completed')) {
      set({ build: { ...build, status: 'completed', percent: 100, versionId: typeof data.version_id === 'string' ? data.version_id : null } });
    } else if (event.endsWith(':failed')) {
      const error = (data.error as { message?: string } | undefined)?.message ?? 'The build failed.';
      set({ build: { ...build, status: 'failed', error } });
    }
  },
  pollBuild: async () => {
    const build = get().build;
    if (!build || ['completed', 'failed', 'cancelled'].includes(build.status)) return;
    try {
      const job = await jobsApi.get(build.jobId);
      const result = (job.result ?? {}) as { version_id?: string; error?: { message?: string } };
      set({
        build: {
          ...build,
          status: job.status,
          percent: job.progress,
          versionId: result.version_id ?? build.versionId,
          error: job.status === 'failed' ? (result.error?.message ?? job.error ?? 'The build failed.') : null,
        },
      });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  verifyRebuild: async (id) => {
    try {
      const { job_id } = await versionsApi.verify(id);
      return job_id;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  deleteVersion: async (id, reason) => {
    try {
      const version = await versionsApi.remove(id, reason);
      set((s) => ({ versions: { ...s.versions, [id]: version } }));
      return true;
    } catch (e) {
      set({ error: message(e) });
      return false;
    }
  },
}));

// Jobs: active and failed operations read from dw_jobs (ADR-007). Live progress arrives on each
// job's Socket.IO room; when the socket is down the hook in hooks/useJobRooms.ts polls instead.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { jobsApi } from '@/api/endpoints';
import type { Job } from '@/types/api';

interface JobEvent {
  job_id?: unknown;
  progress?: unknown;
  status?: unknown;
  rows_done?: unknown;
  rows_total?: unknown;
}

const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

/** A job's progress from an event: the event's own figure, else rows done over rows total. */
export function eventProgress(event: JobEvent): number | undefined {
  const given = num(event.progress);
  if (given !== undefined) return given;
  const done = num(event.rows_done);
  const total = num(event.rows_total);
  return done !== undefined && total ? Math.min(100, (100 * done) / total) : undefined;
}

/** A poll must not move a running job's bar backwards: the row is written at most once a minute,
 * while the job's room reports every few seconds (re-walk finding 4). Live figures are kept until
 * the row catches up. */
function keepLive(previous: Job[], next: Job[]): Job[] {
  const before = new Map(previous.map((j) => [j.id, j]));
  return next.map((job) => {
    const old = before.get(job.id);
    if (!old || old.status !== job.status || old.progress <= job.progress) return job;
    return { ...job, progress: old.progress, rows_done: old.rows_done, rows_total: old.rows_total };
  });
}

interface JobsState {
  active: Job[];
  failed: Job[];
  loading: boolean;
  /** The last action's refusal (cancel, dismiss, start). Kept until the next action, so a poll
   * cannot wipe it before the operator reads it. */
  error: string | null;
  /** Why the job list could not be loaded. */
  loadError: string | null;
  lastNotice: string | null;
  fetchJobs: () => Promise<void>;
  cancelJob: (id: string) => Promise<void>;
  dismissJob: (id: string) => Promise<void>;
  /** Dismiss several failures, then reload once; the first refusal is kept. */
  dismissJobs: (ids: string[]) => Promise<void>;
  startSelftest: (durationSeconds: number) => Promise<Job | null>;
  applyEvent: (event: JobEvent) => void;
}

const message = (error: unknown) => (error instanceof ApiError ? error.message : 'Something went wrong.');

export const useJobsStore = create<JobsState>()((set, get) => ({
  active: [],
  failed: [],
  loading: false,
  error: null,
  loadError: null,
  lastNotice: null,
  fetchJobs: async () => {
    set({ loading: true });
    try {
      const [active, failed] = await Promise.all([jobsApi.list('active'), jobsApi.list('failed')]);
      set((s) => ({ active: keepLive(s.active, active.jobs), failed: failed.jobs, loading: false, loadError: null }));
    } catch (error) {
      set({ loading: false, loadError: message(error) });
    }
  },
  cancelJob: async (id) => {
    set({ error: null });
    try {
      const { detail } = await jobsApi.cancel(id);
      set({ lastNotice: detail });
    } catch (error) {
      set({ error: message(error) });
    }
    await get().fetchJobs();
  },
  dismissJob: async (id) => {
    set({ error: null });
    try {
      await jobsApi.dismiss(id);
    } catch (error) {
      set({ error: message(error) });
    }
    await get().fetchJobs();
  },
  dismissJobs: async (ids) => {
    set({ error: null });
    for (const id of ids) {
      try {
        await jobsApi.dismiss(id);
      } catch (error) {
        set({ error: message(error) });
        break;
      }
    }
    await get().fetchJobs();
  },
  startSelftest: async (durationSeconds) => {
    set({ error: null });
    try {
      const job = await jobsApi.startSelftest(durationSeconds);
      await get().fetchJobs();
      return job;
    } catch (error) {
      set({ error: message(error) });
      return null;
    }
  },
  applyEvent: (event) => {
    if (typeof event.job_id !== 'string') return;
    const id = event.job_id;
    const status = typeof event.status === 'string' ? event.status : undefined;
    if (status && ['completed', 'cancelled', 'failed'].includes(status)) {
      void get().fetchJobs();
      return;
    }
    set((state) => ({
      active: state.active.map((job) =>
        job.id === id
          ? {
              ...job,
              progress: eventProgress(event) ?? job.progress,
              rows_done: num(event.rows_done) ?? job.rows_done,
              rows_total: num(event.rows_total) ?? job.rows_total,
              status: (status as Job['status']) ?? job.status,
            }
          : job,
      ),
    }));
  },
}));

// Calibration (FTDD 006 section 7.2): records grouped by question, sets, targets, and the compute
// jobs being followed. Records are immutable, so a fetched record is cached by id; the status of a
// labeler is never cached (a new record supersedes). Job progress arrives over the job's room
// (hooks/useCalibrationJob.ts) with polling of GET /api/v1/jobs/{id} while disconnected.
import { create } from 'zustand';

import { calibrationApi } from '@/api/calibration';
import { ApiError } from '@/api/client';
import type {
  CalibrationRecord,
  CalibrationSet,
  CalibrationSetImport,
  CalibrationSetPreview,
  TargetsOut,
} from '@/types/calibration';

interface CalibrationState {
  records: CalibrationRecord[];
  sets: CalibrationSet[];
  targets: Record<string, TargetsOut>;
  /** job id -> status, for the compute jobs this session started. */
  jobs: Record<string, string>;
  preview: CalibrationSetPreview | null;
  previewError: string | null;
  error: string | null;
  fetchRecords: () => Promise<void>;
  fetchSets: () => Promise<void>;
  previewMapping: (body: CalibrationSetImport) => Promise<void>;
  importSet: (body: CalibrationSetImport) => Promise<CalibrationSet | null>;
  buildFromReview: (queueId: string) => Promise<CalibrationSet | null>;
  startCompute: (labelRunId: string, calibrationSetId: string) => Promise<string | null>;
  applyJobEvent: (jobId: string, status: string) => void;
  fetchTargets: (questionHash: string) => Promise<void>;
  setTarget: (question: string, questionHash: string, target: number) => Promise<boolean>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export const useCalibrationStore = create<CalibrationState>((set, get) => ({
  records: [],
  sets: [],
  targets: {},
  jobs: {},
  preview: null,
  previewError: null,
  error: null,
  fetchRecords: async () => {
    try {
      const page = await calibrationApi.records();
      set({ records: page.items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchSets: async () => {
    try {
      const page = await calibrationApi.sets();
      set({ sets: page.items });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  previewMapping: async (body) => {
    try {
      set({ preview: await calibrationApi.preview(body), previewError: null });
    } catch (e) {
      set({ preview: null, previewError: message(e) });
    }
  },
  importSet: async (body) => {
    try {
      const created = await calibrationApi.importSet(body);
      set((s) => ({ sets: [created, ...s.sets], error: null }));
      return created;
    } catch (e) {
      set({ previewError: message(e) });
      return null;
    }
  },
  buildFromReview: async (queueId) => {
    try {
      const created = await calibrationApi.fromReview(queueId);
      set((s) => ({ sets: [created, ...s.sets], error: null }));
      return created;
    } catch (e) {
      set({ previewError: message(e) });
      return null;
    }
  },
  startCompute: async (labelRunId, calibrationSetId) => {
    try {
      const job = await calibrationApi.compute(labelRunId, calibrationSetId);
      set((s) => ({ jobs: { ...s.jobs, [job.job_id]: 'queued' }, error: null }));
      return job.job_id;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  applyJobEvent: (jobId, status) => {
    set((s) => ({ jobs: { ...s.jobs, [jobId]: status } }));
    if (status === 'completed') void get().fetchRecords();
  },
  fetchTargets: async (questionHash) => {
    try {
      const targets = await calibrationApi.targets(questionHash);
      set((s) => ({ targets: { ...s.targets, [questionHash]: targets } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  setTarget: async (question, questionHash, target) => {
    try {
      await calibrationApi.setTarget(question, target);
      await get().fetchTargets(questionHash);
      return true;
    } catch (e) {
      set({ error: message(e) });
      return false;
    }
  },
}));

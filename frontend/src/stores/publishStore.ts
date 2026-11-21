// The Publish and export screen's state (008 FTDD section 7; FTASKS 11.6).
//
// Server state is authoritative: the store keeps the current selection and the latest build, check
// run, publishes and exports, each refreshed from REST. ANY change to the version, repository,
// visibility or prose clears the check run, so the public button can never be enabled by checks run
// for something else. Long work is followed through its job: a Socket.IO room while connected
// (hooks/usePublishJob.ts), the job record by polling while not.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { jobsApi } from '@/api/endpoints';
import { publishingApi } from '@/api/publishing';
import type {
  ActiveJob,
  CardDraft,
  CheckRun,
  ExportRecord,
  ExportRequest,
  ModelTerms,
  PublishBuild,
  PublishRecord,
  Visibility,
} from '@/types/publishing';

export interface Selection {
  versionId: string;
  repoId: string;
  visibility: Visibility;
  prose: string;
  labelColumn: string;
}

const EMPTY: Selection = { versionId: '', repoId: '', visibility: 'private', prose: '', labelColumn: '' };
const TERMINAL = ['completed', 'failed', 'cancelled'];

interface PublishState {
  selection: Selection;
  build: PublishBuild | null;
  checkRun: CheckRun | null;
  draft: CardDraft | null;
  job: ActiveJob | null;
  lastPublish: PublishRecord | null;
  publishes: PublishRecord[];
  exports: ExportRecord[];
  terms: ModelTerms | null;
  error: string | null;
  notice: string | null;
  setSelection: (patch: Partial<Selection>) => void;
  previewFiles: () => Promise<void>;
  runChecks: () => Promise<void>;
  publish: () => Promise<void>;
  reverify: (publishId: string) => Promise<void>;
  startExport: (request: ExportRequest) => Promise<void>;
  fetchHistory: () => Promise<void>;
  fetchTerms: (modelId: string) => Promise<void>;
  addTermsNote: (modelId: string, value: 'permits' | 'forbids', text: string) => Promise<boolean>;
  applyJobEvent: (event: string, data: Record<string, unknown>) => void;
  pollJob: () => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');

/** Invalidation rule: these fields decide what the checks were run for. */
export const INVALIDATING: Array<keyof Selection> = ['versionId', 'repoId', 'visibility', 'prose', 'labelColumn'];

export const usePublishStore = create<PublishState>()((set, get) => {
  const finish = async (job: ActiveJob) => {
    const result = { ...job };
    set({ job: result });
    const state = get();
    if (job.kind === 'publish_build' && state.build) {
      const build = await publishingApi.getBuild(state.build.id);
      set({ build });
      if (build.status === 'completed' && state.selection.repoId) {
        set({ draft: await publishingApi.cardDraft(build.version_id, build.id, state.selection.repoId).catch(() => null) });
      }
    } else if (job.kind === 'publish_check' && state.checkRun) {
      set({ checkRun: await publishingApi.getCheckRun(state.checkRun.id) });
    } else if (job.kind === 'publish' || job.kind === 'export' || job.kind === 'publish_reverify') {
      await get().fetchHistory();
      const done = get().publishes.find((p) => p.job_id === job.jobId) ?? null;
      if (done) set({ lastPublish: done });
    }
  };

  return {
    selection: EMPTY,
    build: null,
    checkRun: null,
    draft: null,
    job: null,
    lastPublish: null,
    publishes: [],
    exports: [],
    terms: null,
    error: null,
    notice: null,
    setSelection: (patch) => {
      const current = get().selection;
      const next = { ...current, ...patch };
      const changed = INVALIDATING.some((k) => next[k] !== current[k]);
      const versionChanged = next.versionId !== current.versionId || next.labelColumn !== current.labelColumn;
      set({
        selection: next,
        ...(changed ? { checkRun: null } : {}),
        ...(versionChanged ? { build: null, draft: null } : {}),
      });
    },
    previewFiles: async () => {
      const { versionId, labelColumn } = get().selection;
      if (!versionId) return;
      set({ error: null, notice: null });
      try {
        const accepted = await publishingApi.startBuild(versionId, labelColumn || null);
        const build = await publishingApi.getBuild(accepted.build_id);
        set({ build, checkRun: null });
        if (accepted.job_id) {
          set({ job: { jobId: accepted.job_id, kind: 'publish_build', status: 'queued', progress: 0, message: 'Building files' } });
        } else if (get().selection.repoId) {
          set({ draft: await publishingApi.cardDraft(versionId, build.id, get().selection.repoId).catch(() => null) });
        }
      } catch (e) {
        set({ error: message(e) });
      }
    },
    runChecks: async () => {
      const { selection, build } = get();
      if (!build || build.status !== 'completed' || !selection.repoId) return;
      set({ error: null });
      try {
        const accepted = await publishingApi.startChecks(selection.versionId, {
          build_id: build.id,
          repo_id: selection.repoId,
          visibility: selection.visibility,
        });
        set({
          checkRun: await publishingApi.getCheckRun(accepted.check_run_id),
          job: { jobId: accepted.job_id, kind: 'publish_check', status: 'queued', progress: 0, message: 'Running checks' },
        });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    publish: async () => {
      const { selection, build } = get();
      if (!build) return;
      set({ error: null, notice: null });
      try {
        const accepted = await publishingApi.publish({
          version_id: selection.versionId,
          build_id: build.id,
          repo_id: selection.repoId,
          visibility: selection.visibility,
          card_prose: selection.prose,
        });
        set({
          job: { jobId: accepted.job_id, kind: 'publish', status: 'queued', progress: 0, message: 'Queued' },
          notice: `Publishing ${selection.repoId}. The result is the verification, not the upload.`,
        });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    reverify: async (publishId) => {
      try {
        const { job_id } = await publishingApi.reverify(publishId);
        set({ job: { jobId: job_id, kind: 'publish_reverify', status: 'queued', progress: 0, message: 'Re-verifying' } });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    startExport: async (request) => {
      set({ error: null });
      try {
        const { job_id } = await publishingApi.startExport(request);
        set({ job: { jobId: job_id, kind: 'export', status: 'queued', progress: 0, message: 'Exporting' } });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    fetchHistory: async () => {
      try {
        const [publishes, exports] = await Promise.all([publishingApi.listPublishes(), publishingApi.listExports()]);
        set({ publishes: publishes.items, exports: exports.items });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    fetchTerms: async (modelId) => {
      try {
        set({ terms: await publishingApi.modelTerms(modelId) });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    addTermsNote: async (modelId, value, text) => {
      try {
        await publishingApi.addTermsNote(modelId, { training_on_outputs: value, text });
        set({ terms: await publishingApi.modelTerms(modelId) });
        return true;
      } catch (e) {
        set({ error: message(e) });
        return false;
      }
    },
    applyJobEvent: (event, data) => {
      const job = get().job;
      if (!job || data.job_id !== job.jobId) return;
      if (event.endsWith(':progress')) {
        set({
          job: {
            ...job,
            status: 'running',
            progress: typeof data.progress === 'number' ? data.progress : job.progress,
            message: typeof data.phase === 'string' ? data.phase : job.message,
          },
        });
        return;
      }
      const status = event.split(':')[1];
      if (TERMINAL.includes(status)) void finish({ ...job, status, progress: 100 });
    },
    pollJob: async () => {
      const job = get().job;
      if (!job || TERMINAL.includes(job.status)) return;
      try {
        const row = await jobsApi.get(job.jobId);
        const next = { ...job, status: row.status, progress: row.progress, message: row.message ?? job.message };
        if (TERMINAL.includes(row.status)) await finish(next);
        else set({ job: next });
      } catch (e) {
        set({ error: message(e) });
      }
    },
  };
});

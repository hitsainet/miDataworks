// Sources (001 FTDD section 7). Server state only: form fields stay in their components, and the
// access token never enters this store — it is an argument to previewHf/importHf, sent once.
// On an import's completion the store REFETCHES the source; it never builds one from event fields,
// so the list and the detail come from the backend's one serialiser.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { jobsApi } from '@/api/endpoints';
import { sourcesApi } from '@/api/sources';
import type {
  AnnotationRequest,
  HfPreview,
  HfRequest,
  ImportProgress,
  SourceDetail,
  SourcesMeta,
  SourceSummary,
  UploadManifest,
} from '@/types/sources';

export type ImportOutcome =
  | { kind: 'started'; jobId: string }
  | { kind: 'existing'; source: SourceDetail }
  | { kind: 'approval'; approvalId: string }
  | { kind: 'refused'; code: string; message: string; details: Record<string, unknown> };

interface SourcesState {
  meta: SourcesMeta | null;
  sources: SourceSummary[];
  total: number;
  selected: SourceDetail | null;
  preview: HfPreview | null;
  previewStatus: 'idle' | 'loading' | 'ready' | 'error';
  previewError: string | null;
  imports: Record<string, ImportProgress>;
  loading: boolean;
  error: string | null;
  notice: string | null;
  /** Once per session; `refresh` after a limit changes in Settings. */
  fetchSourcesMeta: (refresh?: boolean) => Promise<void>;
  fetchSources: () => Promise<void>;
  fetchSource: (id: string) => Promise<SourceDetail | null>;
  closeSource: () => void;
  previewHf: (request: HfRequest, token?: string) => Promise<HfPreview | null>;
  closePreview: () => void;
  importHf: (request: HfRequest & { confirm_large?: boolean }, token?: string) => Promise<ImportOutcome>;
  uploadFiles: (files: File[], manifest: UploadManifest) => Promise<ImportOutcome>;
  annotateSource: (id: string, body: AnnotationRequest) => Promise<boolean>;
  overrideDetection: (id: string, value: Record<string, unknown>, reason: string) => Promise<boolean>;
  deleteSource: (id: string, reason: string) => Promise<boolean>;
  applyImportEvent: (event: string, data: Record<string, unknown>) => void;
  pollImports: () => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');
const TERMINAL = ['completed', 'failed', 'cancelled'];

function refusal(e: unknown): ImportOutcome {
  if (e instanceof ApiError) return { kind: 'refused', code: e.code, message: e.message, details: e.details };
  return { kind: 'refused', code: 'UNKNOWN', message: 'Something went wrong.', details: {} };
}

export const useSourcesStore = create<SourcesState>()((set, get) => {
  const track = (jobId: string, label: string) =>
    set((s) => ({
      imports: {
        ...s.imports,
        [jobId]: { jobId, label, status: 'queued', phase: null, progress: 0, sourceId: null, existing: false, error: null },
      },
    }));

  const accepted = async (body: unknown, label: string): Promise<ImportOutcome> => {
    const b = body as Record<string, unknown>;
    if (typeof b.approval_id === 'string') {
      set({ notice: 'Waiting for the operator to approve this in miDataworks.' });
      return { kind: 'approval', approvalId: b.approval_id };
    }
    if (typeof b.job_id === 'string') {
      track(b.job_id, label);
      await get().fetchSources();
      return { kind: 'started', jobId: b.job_id };
    }
    const source = body as SourceDetail;
    set({ notice: `Already imported: ${source.display_name}. Nothing was downloaded.` });
    await get().fetchSources();
    return { kind: 'existing', source };
  };

  return {
    meta: null,
    sources: [],
    total: 0,
    selected: null,
    preview: null,
    previewStatus: 'idle',
    previewError: null,
    imports: {},
    loading: false,
    error: null,
    notice: null,
    fetchSourcesMeta: async (refresh = false) => {
      if (get().meta && !refresh) return;
      try {
        set({ meta: await sourcesApi.meta() });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    fetchSources: async () => {
      set({ loading: true });
      try {
        const page = await sourcesApi.list({ limit: 100 });
        set({ sources: page.items, total: page.total, loading: false, error: null });
      } catch (e) {
        set({ loading: false, error: message(e) });
      }
    },
    fetchSource: async (id) => {
      try {
        const source = await sourcesApi.get(id);
        set({ selected: source, error: null });
        return source;
      } catch (e) {
        set({ error: message(e) });
        return null;
      }
    },
    closeSource: () => set({ selected: null }),
    previewHf: async (request, token) => {
      set({ previewStatus: 'loading', preview: null, previewError: null });
      try {
        const preview = await sourcesApi.preview(request, token);
        if ((preview as unknown as { approval_id?: string }).approval_id) {
          set({ previewStatus: 'error', previewError: 'Waiting for the operator to approve this in miDataworks.' });
          return null;
        }
        set({ preview, previewStatus: 'ready' });
        return preview;
      } catch (e) {
        set({ previewStatus: 'error', previewError: message(e) });
        return null;
      }
    },
    closePreview: () => set({ preview: null, previewStatus: 'idle', previewError: null }),
    importHf: async (request, token) => {
      set({ error: null, notice: null });
      try {
        return await accepted(await sourcesApi.importHf(request, token), request.repo_id);
      } catch (e) {
        return refusal(e);
      }
    },
    uploadFiles: async (files, manifest) => {
      set({ error: null, notice: null });
      try {
        return await accepted(await sourcesApi.upload(files, manifest), manifest.display_name ?? files[0]?.name ?? 'upload');
      } catch (e) {
        return refusal(e);
      }
    },
    annotateSource: async (id, body) => {
      set({ error: null });
      try {
        await sourcesApi.annotate(id, body);
        await get().fetchSource(id);
        return true;
      } catch (e) {
        set({ error: message(e) });
        return false;
      }
    },
    overrideDetection: async (id, value, reason) => get().annotateSource(id, { kind: 'detection_override', value, reason }),
    deleteSource: async (id, reason) => {
      set({ error: null });
      try {
        await sourcesApi.remove(id, reason);
        set({ selected: null, notice: 'Source deleted. Its record and file hashes are kept.' });
        await get().fetchSources();
        return true;
      } catch (e) {
        set({ error: message(e) });
        return false;
      }
    },
    applyImportEvent: (event, data) => {
      const jobId = typeof data.job_id === 'string' ? data.job_id : null;
      if (!jobId || !get().imports[jobId]) return;
      const current = get().imports[jobId];
      if (event === 'source_import:progress') {
        set((s) => ({ imports: { ...s.imports, [jobId]: { ...current, status: 'running', phase: typeof data.phase === 'string' ? data.phase : current.phase } } }));
        return;
      }
      const status = event === 'source_import:completed' ? 'completed' : event === 'source_import:failed' ? 'failed' : 'cancelled';
      const error = data.error as { message?: string } | undefined;
      const sourceId = typeof data.source_id === 'string' ? data.source_id : null;
      set((s) => ({
        imports: {
          ...s.imports,
          [jobId]: { ...current, status, progress: status === 'completed' ? 100 : current.progress, sourceId, existing: data.existing === true, error: error?.message ?? null },
        },
      }));
      // Refetch: the event carries ids only; the source comes from the backend's serialiser.
      void get().fetchSources();
      if (sourceId && get().selected?.id === sourceId) void get().fetchSource(sourceId);
    },
    pollImports: async () => {
      const live = Object.values(get().imports).filter((i) => !TERMINAL.includes(i.status));
      let finished = false;
      for (const item of live) {
        try {
          const job = await jobsApi.get(item.jobId);
          const result = (job.result ?? {}) as { source_id?: string; existing?: boolean };
          if (TERMINAL.includes(job.status)) finished = true;
          set((s) => ({
            imports: {
              ...s.imports,
              [item.jobId]: {
                ...item,
                status: job.status as ImportProgress['status'],
                phase: job.message,
                progress: job.progress,
                sourceId: result.source_id ?? null,
                existing: result.existing === true,
                error: job.status === 'failed' ? job.error : null,
              },
            },
          }));
        } catch (e) {
          set({ error: message(e) });
        }
      }
      if (finished) await get().fetchSources();
    },
  };
});

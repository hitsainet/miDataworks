// Generation runs (FTDD 007 section 7.2): the list, runs by id, records and pairs of the open run,
// templates, the last plan, preview and diversity reports. The socket subscription and the polling
// fallback live in hooks/useGenerationRun.ts.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { generationApi } from '@/api/generation';
import { formRefusal } from '@/api/refusal';
import type { FormRefusal } from '@/api/refusal';
import type {
  CompareResult,
  DiversityReport,
  GenerationPair,
  GenerationRecord,
  GenerationRun,
  GenerationTemplate,
  IndependenceResult,
  Plan,
  Preview,
  RunCreate,
  SteeringSetting,
  TemplateClone,
  TemplateCreate,
} from '@/types/generation';

export interface Refusal {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

interface GenerationState {
  list: GenerationRun[];
  byId: Record<string, GenerationRun>;
  records: Record<string, GenerationRecord[]>;
  pairs: Record<string, GenerationPair[]>;
  live: Record<string, Record<string, unknown>>;
  templates: GenerationTemplate[];
  /** The last create or clone the backend refused, shown by the template form. */
  templateError: FormRefusal | null;
  plan: Plan | null;
  planError: Refusal | null;
  preview: Preview | null;
  compareResult: CompareResult | null;
  independence: IndependenceResult | null;
  diversity: Record<string, DiversityReport | null>;
  selectedId: string | null;
  error: string | null;
  loadRuns: () => Promise<void>;
  loadRun: (id: string) => Promise<GenerationRun | null>;
  loadRecords: (id: string, outcome?: string) => Promise<void>;
  loadPairs: (id: string) => Promise<void>;
  loadTemplates: () => Promise<void>;
  createTemplate: (body: TemplateCreate) => Promise<GenerationTemplate | null>;
  cloneTemplate: (id: string, body: TemplateClone) => Promise<GenerationTemplate | null>;
  planRun: (body: RunCreate) => Promise<Plan | null>;
  start: (body: RunCreate) => Promise<GenerationRun | null>;
  cancel: (id: string) => Promise<void>;
  resume: (id: string) => Promise<void>;
  runPreview: (prompts: string[], setting: SteeringSetting, template?: string | null) => Promise<void>;
  compareSettings: (a: SteeringSetting, b: SteeringSetting) => Promise<void>;
  checkIndependence: (body: { mode: string; generator_setting?: SteeringSetting; setting_a?: SteeringSetting; setting_b?: SteeringSetting }) => Promise<void>;
  buildCandidate: (id: string) => Promise<string | null>;
  loadDiversity: (versionId: string) => Promise<void>;
  requestDiversity: (versionId: string) => Promise<void>;
  selectRun: (id: string | null) => void;
  applyEvent: (runId: string, event: string, data: Record<string, unknown>) => void;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
const refusal = (e: unknown): Refusal =>
  e instanceof ApiError ? { code: e.code, message: e.message, details: e.details } : { code: 'ERROR', message: message(e), details: {} };

/** Replace by id or append, keeping the backend's name-then-version order. */
function upsert(list: GenerationTemplate[], row: GenerationTemplate): GenerationTemplate[] {
  const rest = list.filter((t) => t.id !== row.id);
  return [...rest, row].sort((a, b) => a.name.localeCompare(b.name) || a.version - b.version);
}

export const useGenerationStore = create<GenerationState>((set, get) => ({
  list: [],
  byId: {},
  records: {},
  pairs: {},
  live: {},
  templates: [],
  templateError: null,
  plan: null,
  planError: null,
  preview: null,
  compareResult: null,
  independence: null,
  diversity: {},
  selectedId: null,
  error: null,
  loadRuns: async () => {
    try {
      const page = await generationApi.list();
      set((s) => ({ list: page.items, byId: { ...s.byId, ...Object.fromEntries(page.items.map((r) => [r.id, r])) }, error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  loadRun: async (id) => {
    try {
      const run = await generationApi.get(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)) }));
      return run;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  loadRecords: async (id, outcome) => {
    try {
      const page = await generationApi.records(id, outcome);
      set((s) => ({ records: { ...s.records, [id]: page.items } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  loadPairs: async (id) => {
    try {
      const page = await generationApi.pairs(id);
      set((s) => ({ pairs: { ...s.pairs, [id]: page.items } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  loadTemplates: async () => {
    try {
      const page = await generationApi.templates();
      set({ templates: page.items });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  createTemplate: async (body) => {
    set({ templateError: null });
    try {
      const saved = await generationApi.createTemplate(body);
      set((s) => ({ templates: upsert(s.templates, saved) }));
      return saved;
    } catch (e) {
      set({ templateError: formRefusal(e) });
      return null;
    }
  },
  cloneTemplate: async (id, body) => {
    set({ templateError: null });
    try {
      const saved = await generationApi.cloneTemplate(id, body);
      set((s) => ({ templates: upsert(s.templates, saved) }));
      return saved;
    } catch (e) {
      set({ templateError: formRefusal(e) });
      return null;
    }
  },
  planRun: async (body) => {
    try {
      const plan = await generationApi.plan(body);
      set({ plan, planError: null });
      return plan;
    } catch (e) {
      set({ plan: null, planError: refusal(e) });
      return null;
    }
  },
  start: async (body) => {
    try {
      const run = await generationApi.start(body);
      set((s) => ({ list: [run, ...s.list], byId: { ...s.byId, [run.id]: run }, selectedId: run.id, planError: null }));
      return run;
    } catch (e) {
      set({ planError: refusal(e) });
      return null;
    }
  },
  cancel: async (id) => {
    try {
      const run = await generationApi.cancel(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)), error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  resume: async (id) => {
    try {
      const run = await generationApi.resume(id);
      set((s) => ({ byId: { ...s.byId, [id]: run }, list: s.list.map((r) => (r.id === id ? run : r)), error: null }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  runPreview: async (prompts, setting, template) => {
    try {
      set({ preview: await generationApi.preview(prompts, setting, template), error: null });
    } catch (e) {
      set({ preview: null, error: message(e) });
    }
  },
  compareSettings: async (a, b) => {
    try {
      set({ compareResult: await generationApi.compare(a, b) });
    } catch (e) {
      set({ compareResult: { one_axis: false, differing: [], differing_index: null, not_comparable: [], message: message(e), code: e instanceof ApiError ? e.code : 'ERROR' } });
    }
  },
  checkIndependence: async (body) => {
    try {
      set({ independence: await generationApi.independence(body) });
    } catch (e) {
      set({ independence: null, error: message(e) });
    }
  },
  buildCandidate: async (id) => {
    try {
      const out = await generationApi.candidate(id);
      return out.job_id ?? out.id ?? null;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  loadDiversity: async (versionId) => {
    try {
      const report = await generationApi.diversity(versionId);
      set((s) => ({ diversity: { ...s.diversity, [versionId]: report } }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        set((s) => ({ diversity: { ...s.diversity, [versionId]: null } }));
        return;
      }
      set({ error: message(e) });
    }
  },
  requestDiversity: async (versionId) => {
    try {
      await generationApi.requestDiversity(versionId);
      set({ error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  selectRun: (id) => set({ selectedId: id }),
  applyEvent: (runId, event, data) => {
    if (event === 'generation_run:progress') {
      set((s) => ({ live: { ...s.live, [runId]: data } }));
      return;
    }
    void get().loadRun(runId);
  },
}));

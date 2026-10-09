// Templates, rubrics, "Try it on a sample", the keep-share estimate, the plan and the start
// (FTDD 005 section 7.2). Sample results are never persisted.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { labelingApi } from '@/api/labeling';
import { formRefusal, localRefusal } from '@/api/refusal';
import type { FormRefusal } from '@/api/refusal';
import { downloadBlob } from '@/stores/recipesStore';
import type {
  ApprovalAccepted,
  CalibrationStatus,
  DecisionTemplate,
  EndpointTestResult,
  KeepShareResult,
  LabelRun,
  LabelRunStart,
  Plan,
  LinkChecks,
  ProbeList,
  ReproductionLink,
  ReproductionLinkStart,
  ReproductionRefusal,
  Rubric,
  RubricBody,
  RubricCreate,
  SampleResult,
} from '@/types/labeling';

export const KEEP_SHARE_POLL_MS = 1500;

interface LabelingState {
  templates: DecisionTemplate[];
  rubrics: Rubric[];
  /** The last rubric create, clone, import or export that was refused. */
  rubricError: FormRefusal | null;
  probes: ProbeList | null;
  probesError: string | null;
  /** miLLM is not configured (PROBE_ENDPOINT_UNCONFIGURED): the probe option says so. */
  probesUnconfigured: boolean;
  /** A plan refused REPRODUCTION_UNAVAILABLE, with the facts the plan learned before refusing. */
  planRefusal: ReproductionRefusal | null;
  link: ReproductionLink | null;
  linkError: { message: string; checks: LinkChecks | null } | null;
  linkApproval: ApprovalAccepted | null;
  linkBusy: boolean;
  sample: SampleResult | null;
  sampleBusy: boolean;
  keepShare: KeepShareResult | null;
  plan: Plan | null;
  planError: string | null;
  started: LabelRun | null;
  approval: ApprovalAccepted | null;
  tests: Record<string, EndpointTestResult>;
  calibration: Record<string, CalibrationStatus>;
  error: string | null;
  fetchTemplates: () => Promise<void>;
  fetchRubrics: () => Promise<void>;
  createRubric: (body: RubricCreate) => Promise<Rubric | null>;
  cloneRubric: (id: string, body: RubricBody | null) => Promise<Rubric | null>;
  /** Reads a `midataworks.rubric/v1` file; a file that is not JSON is refused before any request. */
  importRubric: (file: File) => Promise<Rubric | null>;
  /** Downloads the rubric as a `midataworks.rubric/v1` file. */
  exportRubric: (rubric: Rubric) => Promise<boolean>;
  fetchProbes: () => Promise<void>;
  runSample: (body: LabelRunStart, rows?: number) => Promise<void>;
  startKeepShare: (body: LabelRunStart) => Promise<void>;
  pollKeepShare: () => Promise<void>;
  fetchPlan: (body: LabelRunStart) => Promise<void>;
  startRun: (body: LabelRunStart) => Promise<LabelRun | null>;
  testEndpoint: (role: string) => Promise<void>;
  fetchCalibration: (identityHash: string) => Promise<void>;
  createLink: (body: ReproductionLinkStart) => Promise<ReproductionLink | null>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

function sampleBody(body: LabelRunStart, rows: number): Record<string, unknown> {
  return {
    input_version_id: body.input_version_id,
    role: body.role,
    template_id: body.template_id,
    rubric_id: body.rubric_id,
    question: body.question,
    field_map: body.field_map,
    threshold_positive: body.threshold_positive,
    threshold_negative: body.threshold_negative,
    min_top_probability: body.min_top_probability,
    rows,
  };
}

/** A file's text through FileReader, which every browser (and jsdom) has. */
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('the file could not be read'));
    reader.readAsText(file);
  });
}

/** Replace by id or append, keeping the backend's name-then-version order. */
function upsertRubric(list: Rubric[], row: Rubric): Rubric[] {
  const rest = list.filter((r) => r.id !== row.id);
  return [...rest, row].sort((a, b) => a.name.localeCompare(b.name) || a.version - b.version);
}

export const useLabelingStore = create<LabelingState>((set, get) => ({
  templates: [],
  rubrics: [],
  rubricError: null,
  probes: null,
  probesError: null,
  probesUnconfigured: false,
  planRefusal: null,
  link: null,
  linkError: null,
  linkApproval: null,
  linkBusy: false,
  sample: null,
  sampleBusy: false,
  keepShare: null,
  plan: null,
  planError: null,
  started: null,
  approval: null,
  tests: {},
  calibration: {},
  error: null,
  fetchTemplates: async () => {
    try {
      set({ templates: await labelingApi.templates() });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchRubrics: async () => {
    try {
      set({ rubrics: await labelingApi.rubrics() });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  createRubric: async (body) => {
    set({ rubricError: null });
    try {
      const saved = await labelingApi.createRubric(body);
      set((s) => ({ rubrics: upsertRubric(s.rubrics, saved) }));
      return saved;
    } catch (e) {
      set({ rubricError: formRefusal(e) });
      return null;
    }
  },
  cloneRubric: async (id, body) => {
    set({ rubricError: null });
    try {
      const saved = await labelingApi.cloneRubric(id, body);
      set((s) => ({ rubrics: upsertRubric(s.rubrics, saved) }));
      return saved;
    } catch (e) {
      set({ rubricError: formRefusal(e) });
      return null;
    }
  },
  importRubric: async (file) => {
    set({ rubricError: null });
    let document: unknown;
    try {
      document = JSON.parse(await readText(file));
    } catch (e) {
      set({ rubricError: localRefusal(`${file.name} is not JSON: ${e instanceof Error ? e.message : String(e)}`) });
      return null;
    }
    try {
      const saved = await labelingApi.importRubric(document);
      set((s) => ({ rubrics: upsertRubric(s.rubrics, saved) }));
      return saved;
    } catch (e) {
      set({ rubricError: formRefusal(e) });
      return null;
    }
  },
  exportRubric: async (rubric) => {
    set({ rubricError: null });
    try {
      const doc = await labelingApi.exportRubric(rubric.id);
      const blob = new Blob([`${JSON.stringify(doc, null, 2)}\n`], { type: 'application/json' });
      downloadBlob(blob, `${doc.name.replace(/\//g, '_')}@${doc.version}.rubric.json`);
      return true;
    } catch (e) {
      set({ rubricError: formRefusal(e) });
      return false;
    }
  },
  fetchProbes: async () => {
    try {
      set({ probes: await labelingApi.probes(), probesError: null, probesUnconfigured: false });
    } catch (e) {
      // The server's own message (e.g. miLLM unconfigured), verbatim.
      set({
        probes: null,
        probesError: message(e),
        probesUnconfigured: e instanceof ApiError && e.code === 'PROBE_ENDPOINT_UNCONFIGURED',
      });
    }
  },
  runSample: async (body, rows = 5) => {
    set({ sampleBusy: true, error: null });
    try {
      set({ sample: await labelingApi.sample(sampleBody(body, rows)) });
    } catch (e) {
      set({ error: message(e), sample: null });
    } finally {
      set({ sampleBusy: false });
    }
  },
  startKeepShare: async (body) => {
    try {
      const accepted = await labelingApi.startKeepShare({
        input_version_id: body.input_version_id,
        template_id: body.template_id,
        question: body.question,
        field_map: body.field_map,
        threshold_positive: body.threshold_positive,
        threshold_negative: body.threshold_negative,
        min_top_probability: body.min_top_probability,
        row_filter: body.row_filter,
      });
      set({ keepShare: { job_id: accepted.job_id, status: 'queued', share: null, lo: null, hi: null, n: null, seed: null, probabilities: null, error: null }, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  pollKeepShare: async () => {
    const current = get().keepShare;
    if (!current) return;
    try {
      set({ keepShare: await labelingApi.keepShare(current.job_id) });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchPlan: async (body) => {
    try {
      set({ plan: await labelingApi.plan(body), planError: null, planRefusal: null });
    } catch (e) {
      const refusal =
        e instanceof ApiError && e.code === 'REPRODUCTION_UNAVAILABLE' && e.details.probe
          ? ({ ...(e.details as unknown as ReproductionRefusal), message: e.message })
          : null;
      set({ plan: null, planError: message(e), planRefusal: refusal });
    }
  },
  startRun: async (body) => {
    try {
      const keep = get().keepShare;
      // A probe-verdict run carries no keep-share estimate: the server refuses the key (009).
      const answer = await labelingApi.start(
        body.role === 'probe' ? body : { ...body, keep_share_job_id: keep?.status === 'completed' ? keep.job_id : null },
      );
      if ('approval_id' in answer && !('id' in answer)) {
        set({ approval: answer, started: null, error: null });
        return null;
      }
      set({ started: answer as LabelRun, approval: null, error: null });
      return answer as LabelRun;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  testEndpoint: async (role) => {
    try {
      const result = await labelingApi.testEndpoint(role);
      set((s) => ({ tests: { ...s.tests, [role]: result } }));
    } catch (e) {
      set((s) => ({
        tests: {
          ...s.tests,
          [role]: {
            role, reachable: false, model_listed: null, protocol_ok: null, server_kind: null, resident_model: null,
            lease_supported: null, lease_state: null, queue: null,
            error_code: e instanceof ApiError ? e.code : 'ERROR', message: message(e),
          },
        },
      }));
    }
  },
  createLink: async (body) => {
    set({ linkBusy: true, linkError: null, link: null, linkApproval: null });
    try {
      const answer = await labelingApi.createLink(body);
      if ('approval_id' in answer && !('id' in answer)) {
        set({ linkApproval: answer as ApprovalAccepted, linkBusy: false });
        return null;
      }
      set({ link: answer as ReproductionLink, linkBusy: false });
      return answer as ReproductionLink;
    } catch (e) {
      const checks = e instanceof ApiError ? ((e.details.checks as LinkChecks | undefined) ?? null) : null;
      set({ linkError: { message: message(e), checks }, linkBusy: false });
      return null;
    }
  },
  fetchCalibration: async (identityHash) => {
    try {
      const status = await labelingApi.calibrationStatus(identityHash);
      set((s) => ({ calibration: { ...s.calibration, [identityHash]: status } }));
    } catch (e) {
      // The lookup failed: there is no calibration record to show, never a pass.
      if (!(e instanceof ApiError && e.status === 404)) set({ error: message(e) });
      const none: CalibrationStatus = { status: 'none_recorded', record_id: null, verdict: null, rule: null, auroc: null, calibration_set: null };
      set((s) => ({ calibration: { ...s.calibration, [identityHash]: none } }));
    }
  },
}));

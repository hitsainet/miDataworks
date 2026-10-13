// Feature 004's store (FTDD 004 section 7.2). Reports are cached by version (versions are immutable);
// warnings are NEVER cached apart from the audit read that carries them, because they depend on the
// level in force (P-19). A run answered 202 is followed by polling its job record, then refetched.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { curationApi } from '@/api/curation';
import { versionsApi } from '@/api/datasets';
import { jobsApi } from '@/api/endpoints';
import { operatorsApi } from '@/api/operators';
import { recipesApi } from '@/api/recipes';
import type {
  AuditView,
  BalancerReport,
  CellSamples,
  DatasetLevel,
  GlobalLevel,
  Leakage,
  LeakagePair,
  Profile,
  RunOutcome,
} from '@/types/curation';

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');
const cellKey = (id: string, column: string, value: string, label: string) => `${id}|${column}|${value}|${label}`;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
export const JOB_POLL_MS = 2000;

export interface BalancerPreview {
  report: BalancerReport;
  counts: { in: number; kept: number; dropped: number };
}

interface CurationState {
  auditsByVersion: Record<string, AuditView | null>;
  profilesByVersion: Record<string, Profile | null>;
  leakageByVersion: Record<string, Leakage | null>;
  pairsByVersion: Record<string, LeakagePair[]>;
  levelsByDataset: Record<string, DatasetLevel>;
  globalLevel: GlobalLevel | null;
  cellSamples: Record<string, CellSamples>;
  balancer: BalancerPreview | null;
  running: Record<string, boolean>;
  errors: Record<string, string | null>;
  fetchAudit: (versionId: string) => Promise<void>;
  runAudit: (versionId: string, labelColumn?: string) => Promise<void>;
  fetchCellSamples: (versionId: string, column: string, value: string, label: string) => Promise<void>;
  fetchProfile: (versionId: string) => Promise<void>;
  runProfile: (versionId: string, sampleSize?: number) => Promise<void>;
  fetchLeakage: (versionId: string) => Promise<void>;
  runLeakage: (versionId: string, groupColumn?: string) => Promise<void>;
  fetchLevel: (datasetId: string) => Promise<void>;
  setDatasetLevel: (datasetId: string, marginPp: number, reason: string) => Promise<boolean>;
  clearDatasetLevel: (datasetId: string, reason: string) => Promise<boolean>;
  fetchGlobalLevel: () => Promise<void>;
  setGlobalLevel: (marginPp: number, reason: string) => Promise<boolean>;
  previewCellBalancer: (versionId: string, column: string, excludeValues: string[], labelColumn: string, excludeColumns: string[]) => Promise<void>;
  buildCellBalancedVersion: (
    versionId: string,
    datasetId: string,
    column: string,
    excludeValues: string[],
    labelColumn: string,
    excludeColumns: string[],
  ) => Promise<string | null>;
}

async function settle(outcome: RunOutcome): Promise<void> {
  if (outcome.outcome !== 'started' && outcome.outcome !== 'running') return;
  for (let i = 0; i < 300; i++) {
    const job = await jobsApi.get(outcome.job_id);
    if (['completed', 'failed', 'cancelled'].includes(job.status)) return;
    await sleep(JOB_POLL_MS);
  }
}

export const useCurationStore = create<CurationState>()((set, get) => ({
  auditsByVersion: {},
  profilesByVersion: {},
  leakageByVersion: {},
  pairsByVersion: {},
  levelsByDataset: {},
  globalLevel: null,
  cellSamples: {},
  balancer: null,
  running: {},
  errors: {},

  fetchAudit: async (versionId) => {
    try {
      const view = await curationApi.audit(versionId);
      set((s) => ({ auditsByVersion: { ...s.auditsByVersion, [versionId]: view }, errors: { ...s.errors, audit: null } }));
    } catch (e) {
      if (e instanceof ApiError && e.code === 'audit_not_run') {
        set((s) => ({ auditsByVersion: { ...s.auditsByVersion, [versionId]: null } }));
        return;
      }
      set((s) => ({ errors: { ...s.errors, audit: message(e) } }));
    }
  },

  runAudit: async (versionId, labelColumn) => {
    set((s) => ({ running: { ...s.running, audit: true }, errors: { ...s.errors, audit: null } }));
    try {
      await settle(await curationApi.runAudit(versionId, labelColumn ? { label_column: labelColumn } : {}));
      await get().fetchAudit(versionId);
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, audit: message(e) } }));
    } finally {
      set((s) => ({ running: { ...s.running, audit: false } }));
    }
  },

  fetchCellSamples: async (versionId, column, value, label) => {
    try {
      const page = await curationApi.cells(versionId, { column, value, label });
      set((s) => ({ cellSamples: { ...s.cellSamples, [cellKey(versionId, column, value, label)]: page } }));
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, cells: message(e) } }));
    }
  },

  fetchProfile: async (versionId) => {
    try {
      const report = await curationApi.profile(versionId);
      set((s) => ({ profilesByVersion: { ...s.profilesByVersion, [versionId]: report.result as unknown as Profile } }));
    } catch (e) {
      if (e instanceof ApiError && e.code === 'profile_not_run') {
        set((s) => ({ profilesByVersion: { ...s.profilesByVersion, [versionId]: null } }));
        return;
      }
      set((s) => ({ errors: { ...s.errors, profile: message(e) } }));
    }
  },

  runProfile: async (versionId, sampleSize) => {
    set((s) => ({ running: { ...s.running, profile: true }, errors: { ...s.errors, profile: null } }));
    try {
      await settle(await curationApi.runProfile(versionId, sampleSize ? { sample_size: sampleSize } : {}));
      await get().fetchProfile(versionId);
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, profile: message(e) } }));
    } finally {
      set((s) => ({ running: { ...s.running, profile: false } }));
    }
  },

  fetchLeakage: async (versionId) => {
    try {
      const report = await curationApi.leakage(versionId);
      const pairs = await curationApi.leakagePairs(versionId);
      set((s) => ({
        leakageByVersion: { ...s.leakageByVersion, [versionId]: report.result as unknown as Leakage },
        pairsByVersion: { ...s.pairsByVersion, [versionId]: pairs.pairs },
      }));
    } catch (e) {
      if (e instanceof ApiError && e.code === 'leakage_not_run') {
        set((s) => ({ leakageByVersion: { ...s.leakageByVersion, [versionId]: null } }));
        return;
      }
      set((s) => ({ errors: { ...s.errors, leakage: message(e) } }));
    }
  },

  runLeakage: async (versionId, groupColumn) => {
    set((s) => ({ running: { ...s.running, leakage: true }, errors: { ...s.errors, leakage: null } }));
    try {
      await settle(await curationApi.runLeakage(versionId, groupColumn ? { group_column: groupColumn } : {}));
      await get().fetchLeakage(versionId);
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, leakage: message(e) } }));
    } finally {
      set((s) => ({ running: { ...s.running, leakage: false } }));
    }
  },

  fetchLevel: async (datasetId) => {
    try {
      const level = await curationApi.datasetLevel(datasetId);
      set((s) => ({ levelsByDataset: { ...s.levelsByDataset, [datasetId]: level } }));
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, level: message(e) } }));
    }
  },

  setDatasetLevel: async (datasetId, marginPp, reason) => {
    try {
      const level = await curationApi.setDatasetLevel(datasetId, marginPp, reason);
      set((s) => ({ levelsByDataset: { ...s.levelsByDataset, [datasetId]: level }, errors: { ...s.errors, level: null } }));
      return true;
    } catch (e) {
      const text = e instanceof ApiError && e.status === 403 ? 'Only the operator can change this level.' : message(e);
      set((s) => ({ errors: { ...s.errors, level: text } }));
      return false;
    }
  },

  clearDatasetLevel: async (datasetId, reason) => {
    try {
      const level = await curationApi.clearDatasetLevel(datasetId, reason);
      set((s) => ({ levelsByDataset: { ...s.levelsByDataset, [datasetId]: level }, errors: { ...s.errors, level: null } }));
      return true;
    } catch (e) {
      const text = e instanceof ApiError && e.status === 403 ? 'Only the operator can change this level.' : message(e);
      set((s) => ({ errors: { ...s.errors, level: text } }));
      return false;
    }
  },

  fetchGlobalLevel: async () => {
    try {
      set({ globalLevel: await curationApi.globalLevel() });
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, level: message(e) } }));
    }
  },

  setGlobalLevel: async (marginPp, reason) => {
    try {
      set({ globalLevel: await curationApi.setGlobalLevel(marginPp, reason) });
      return true;
    } catch (e) {
      const text = e instanceof ApiError && e.status === 403 ? 'Only the operator can change this level.' : message(e);
      set((s) => ({ errors: { ...s.errors, level: text } }));
      return false;
    }
  },

  previewCellBalancer: async (versionId, column, excludeValues, labelColumn, excludeColumns) => {
    set((s) => ({ running: { ...s.running, balancer: true }, errors: { ...s.errors, balancer: null }, balancer: null }));
    try {
      const result = (await operatorsApi.preview('cell_balancer', '1.0.0', {
        params: { column, exclude_values: excludeValues, label_column: labelColumn, exclude_columns: excludeColumns },
        input: { version_id: versionId },
        sample_size: 2000,
      })) as unknown as { report?: BalancerReport; counts: { in: number; kept: number; dropped: number } };
      if (!result.report) throw new Error('The preview returned no balancer report.');
      set({ balancer: { report: result.report, counts: result.counts } });
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, balancer: e instanceof Error ? e.message : message(e) } }));
    } finally {
      set((s) => ({ running: { ...s.running, balancer: false } }));
    }
  },

  buildCellBalancedVersion: async (versionId, datasetId, column, excludeValues, labelColumn, excludeColumns) => {
    try {
      const recipe = await recipesApi.create({
        name: `${column}-balanced-${versionId.slice(0, 8)}`,
        description: `Cell-balanced on ${column} (feature 004), from version ${versionId}.`,
        body: {
          format: 'dw.recipe/v1',
          steps: [
            {
              operator: 'cell_balancer',
              version: '1.0.0',
              params: { column, exclude_values: excludeValues, label_column: labelColumn, exclude_columns: excludeColumns },
            },
          ],
        },
      });
      if (!recipe.head_revision_id) throw new Error('The new recipe has no revision.');
      const built = await versionsApi.build({
        dataset_id: datasetId,
        inputs: [{ kind: 'version', version_id: versionId }],
        recipe_revision_id: recipe.head_revision_id,
      });
      return 'job_id' in built ? built.job_id : built.id;
    } catch (e) {
      set((s) => ({ errors: { ...s.errors, balancer: message(e) } }));
      return null;
    }
  },
}));

export { cellKey };

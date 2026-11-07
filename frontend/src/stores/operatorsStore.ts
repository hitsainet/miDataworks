// Feature 003's store (FTDD 003 section 7.2): catalogue with filters, the selected operator, the
// allowlist, and previews and statistics keyed by their request, each with its own loading and
// error. A slider move never comes here: the threshold control recomputes locally.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { operatorsApi, type ListFilters } from '@/api/operators';
import type {
  AllowlistChange,
  AllowlistItem,
  FieldError,
  OperatorEntry,
  OperatorState,
  PreviewRequest,
  PreviewResult,
  SchemaSubset,
  StatisticsResult,
} from '@/types/operators';

export interface Keyed<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
}

interface OperatorsState {
  catalogue: OperatorEntry[];
  summary: Partial<Record<OperatorState, number>>;
  filters: ListFilters;
  catalogueLoading: boolean;
  catalogueError: string | null;
  subset: SchemaSubset | null;
  selected: OperatorEntry | null;
  selectedError: string | null;
  fieldErrors: FieldError[];
  allowlist: AllowlistItem[];
  allowlistError: string | null;
  previews: Record<string, Keyed<PreviewResult>>;
  statistics: Record<string, Keyed<StatisticsResult>>;
  fetchCatalogue: () => Promise<void>;
  setFilter: (key: keyof ListFilters, value: string) => Promise<void>;
  fetchSubset: () => Promise<void>;
  selectOperator: (name: string, version: string) => Promise<void>;
  clearSelection: () => void;
  validateParams: (name: string, version: string, params: Record<string, unknown>) => Promise<boolean>;
  runPreview: (name: string, version: string, request: PreviewRequest) => Promise<string>;
  fetchStatistics: (name: string, version: string, request: PreviewRequest) => Promise<string>;
  fetchAllowlist: () => Promise<void>;
  changeAllowlist: (action: 'allow' | 'revoke', change: AllowlistChange) => Promise<boolean>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Something went wrong.');

/** The request's identity on the client: identical previews share one entry. */
export const requestKey = (name: string, version: string, request: PreviewRequest) =>
  JSON.stringify([name, version, request.params, request.input, request.sample_size ?? null, request.seed ?? 0]);

export const useOperatorsStore = create<OperatorsState>()((set, get) => ({
  catalogue: [],
  summary: {},
  filters: {},
  catalogueLoading: false,
  catalogueError: null,
  subset: null,
  selected: null,
  selectedError: null,
  fieldErrors: [],
  allowlist: [],
  allowlistError: null,
  previews: {},
  statistics: {},
  fetchCatalogue: async () => {
    set({ catalogueLoading: true });
    try {
      const list = await operatorsApi.list(get().filters);
      set({ catalogue: list.items, summary: list.summary, catalogueLoading: false, catalogueError: null });
    } catch (e) {
      set({ catalogueLoading: false, catalogueError: message(e) });
    }
  },
  setFilter: async (key, value) => {
    set({ filters: { ...get().filters, [key]: value || undefined } });
    await get().fetchCatalogue();
  },
  fetchSubset: async () => {
    if (get().subset) return;
    try {
      set({ subset: await operatorsApi.schemaSubset() });
    } catch (e) {
      set({ catalogueError: message(e) });
    }
  },
  selectOperator: async (name, version) => {
    set({ selectedError: null, fieldErrors: [] });
    try {
      set({ selected: await operatorsApi.get(name, version) });
    } catch (e) {
      set({ selected: null, selectedError: message(e) });
    }
  },
  clearSelection: () => set({ selected: null, selectedError: null, fieldErrors: [] }),
  validateParams: async (name, version, params) => {
    try {
      await operatorsApi.validate(name, version, params);
      set({ fieldErrors: [] });
      return true;
    } catch (e) {
      const errors = e instanceof ApiError && e.code === 'params_invalid' ? ((e.details.errors as FieldError[]) ?? []) : [];
      set({ fieldErrors: errors.length ? errors : [{ pointer: '', message: message(e) }] });
      return false;
    }
  },
  runPreview: async (name, version, request) => {
    const key = requestKey(name, version, request);
    set({ previews: { ...get().previews, [key]: { loading: true, error: null, data: null } } });
    try {
      const data = await operatorsApi.preview(name, version, request);
      set({ previews: { ...get().previews, [key]: { loading: false, error: null, data } } });
    } catch (e) {
      set({ previews: { ...get().previews, [key]: { loading: false, error: message(e), data: null } } });
    }
    return key;
  },
  fetchStatistics: async (name, version, request) => {
    const key = requestKey(name, version, request);
    if (get().statistics[key]?.data) return key;
    set({ statistics: { ...get().statistics, [key]: { loading: true, error: null, data: null } } });
    try {
      const data = await operatorsApi.statistics(name, version, request);
      set({ statistics: { ...get().statistics, [key]: { loading: false, error: null, data } } });
    } catch (e) {
      set({ statistics: { ...get().statistics, [key]: { loading: false, error: message(e), data: null } } });
    }
    return key;
  },
  fetchAllowlist: async () => {
    try {
      const { items } = await operatorsApi.allowlist();
      set({ allowlist: items, allowlistError: null });
    } catch (e) {
      set({ allowlistError: message(e) });
    }
  },
  changeAllowlist: async (action, change) => {
    try {
      await (action === 'allow' ? operatorsApi.allow(change) : operatorsApi.revoke(change));
      set({ allowlistError: null });
      await Promise.all([get().fetchAllowlist(), get().fetchCatalogue()]);
      return true;
    } catch (e) {
      set({ allowlistError: message(e) });
      return false;
    }
  },
}));

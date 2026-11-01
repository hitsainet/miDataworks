// The Code rules screen's state (feature 018, FR-018.44). Server state is authoritative: each
// mutation is ONE request, then the list and the open version are read again. A refused Revoke or
// Approve again keeps the server's code, message and details (a failing case under the current
// contract, the runner unavailable), so the screen can say exactly what happened; nothing changed.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { codeRulesApi } from '@/api/codeRules';
import type { CaseResult, CodeRuleDetail, CodeRuleSummary } from '@/types/codeRules';

export interface CodeRuleRefusal {
  status: number;
  code: string;
  message: string;
  details: Record<string, unknown>;
}

interface CodeRulesState {
  items: CodeRuleSummary[];
  filter: { behaviourKey: string; includeArchived: boolean };
  current: CodeRuleDetail | null;
  /** Code rules switched on in this deployment; null while unknown. */
  enabled: boolean | null;
  error: string | null;
  /** The last Revoke / Approve again refusal, as the server gave it. */
  refusal: CodeRuleRefusal | null;
  /** Case results the last successful Approve again ran. */
  lastCaseResults: CaseResult[] | null;
  notice: string | null;
  fetchList: (behaviourKey: string, includeArchived: boolean) => Promise<void>;
  open: (name: string, version: number) => Promise<void>;
  fetchContract: () => Promise<void>;
  revoke: (sha: string, reason: string) => Promise<boolean>;
  approveAgain: (sha: string, reason: string) => Promise<boolean>;
  archive: (name: string) => Promise<void>;
  unarchive: (name: string) => Promise<void>;
  clearRefusal: () => void;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
const refusalOf = (e: unknown): CodeRuleRefusal =>
  e instanceof ApiError
    ? { status: e.status, code: e.code, message: e.message, details: e.details }
    : { status: 0, code: 'ERROR', message: message(e), details: {} };

export const useCodeRulesStore = create<CodeRulesState>((set, get) => {
  const refresh = async () => {
    const { behaviourKey, includeArchived } = get().filter;
    await get().fetchList(behaviourKey, includeArchived);
    const current = get().current;
    if (current) await get().open(current.name, current.version);
  };
  const decide = async (kind: 'revoke' | 'approveAgain', sha: string, reason: string) => {
    set({ refusal: null, notice: null, lastCaseResults: null });
    try {
      const out = await codeRulesApi[kind](sha, reason.trim());
      set({
        lastCaseResults: out.case_results ?? null,
        notice: out.changed
          ? kind === 'revoke'
            ? `Revoked ${out.name} version ${out.version}. Rule set previews and builds refuse this code from now on.`
            : `Approved ${out.name} version ${out.version} again; every stored case passed.`
          : `Nothing changed: the code is ${out.state}.`,
      });
      await refresh();
      return true;
    } catch (e) {
      set({ refusal: refusalOf(e) });
      return false;
    }
  };
  return {
    items: [],
    filter: { behaviourKey: '', includeArchived: false },
    current: null,
    enabled: null,
    error: null,
    refusal: null,
    lastCaseResults: null,
    notice: null,
    fetchList: async (behaviourKey, includeArchived) => {
      set({ filter: { behaviourKey, includeArchived } });
      try {
        const out = await codeRulesApi.list({ behaviourKey: behaviourKey.trim() || undefined, includeArchived });
        set({ items: out.items, error: null });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    open: async (name, version) => {
      try {
        set({ current: await codeRulesApi.get(name, version), error: null });
      } catch (e) {
        set({ error: message(e) });
      }
    },
    fetchContract: async () => {
      try {
        set({ enabled: (await codeRulesApi.contract()).code_rules_enabled });
      } catch {
        set({ enabled: null });
      }
    },
    revoke: (sha, reason) => decide('revoke', sha, reason),
    approveAgain: (sha, reason) => decide('approveAgain', sha, reason),
    archive: async (name) => {
      try {
        await codeRulesApi.archive(name);
        await refresh();
      } catch (e) {
        set({ error: message(e) });
      }
    },
    unarchive: async (name) => {
      try {
        await codeRulesApi.unarchive(name);
        await refresh();
      } catch (e) {
        set({ error: message(e) });
      }
    },
    clearRefusal: () => set({ refusal: null }),
  };
});

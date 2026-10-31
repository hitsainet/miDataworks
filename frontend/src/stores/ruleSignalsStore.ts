// The Rule signals screen's state (feature 016). Server state is authoritative and read-only here:
// rule sets are written through MCP or REST (D22). The preview runs ONLY when the operator clicks
// Preview; no effect sends the text as it is typed.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { ruleSignalsApi } from '@/api/ruleSignals';
import type {
  PreviewResult,
  RuleDerivation,
  RuleSetDetail,
  RuleSetSummary,
  ValidatorDetail,
  ValidatorSummary,
} from '@/types/ruleSignals';

interface RuleSignalsState {
  ruleSets: RuleSetSummary[];
  current: RuleSetDetail | null;
  validators: ValidatorSummary[];
  validator: ValidatorDetail | null;
  preview: PreviewResult | null;
  derivation: RuleDerivation | null;
  error: string | null;
  fetchRuleSets: (behaviourKey: string, includeArchived: boolean) => Promise<void>;
  openRuleSet: (name: string) => Promise<void>;
  /** Close the open rule set's detail (its link again, the X or Escape; operator 2026-10-31). */
  closeRuleSet: () => void;
  fetchValidators: () => Promise<void>;
  openValidator: (id: string, version: number) => Promise<void>;
  closeValidator: () => void;
  runPreview: (text: string) => Promise<void>;
  openDerivation: (id: string) => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export const useRuleSignalsStore = create<RuleSignalsState>((set, get) => ({
  ruleSets: [],
  current: null,
  validators: [],
  validator: null,
  preview: null,
  derivation: null,
  error: null,
  fetchRuleSets: async (behaviourKey, includeArchived) => {
    try {
      const out = await ruleSignalsApi.listRuleSets({ behaviourKey: behaviourKey.trim() || undefined, includeArchived });
      set({ ruleSets: out.items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  openRuleSet: async (name) => {
    try {
      set({ current: await ruleSignalsApi.getRuleSet(name), preview: null, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  closeRuleSet: () => set({ current: null, preview: null }),
  fetchValidators: async () => {
    try {
      set({ validators: (await ruleSignalsApi.listValidators()).items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  openValidator: async (id, version) => {
    try {
      set({ validator: await ruleSignalsApi.getValidator(id, version), error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  closeValidator: () => set({ validator: null }),
  openDerivation: async (id) => {
    try {
      set({ derivation: await ruleSignalsApi.getDerivation(id.trim()), error: null });
    } catch (e) {
      set({ error: message(e), derivation: null });
    }
  },
  runPreview: async (text) => {
    const current = get().current;
    if (!current) return;
    try {
      set({ preview: await ruleSignalsApi.preview(current.name, current.version, text), error: null });
    } catch (e) {
      set({ error: message(e), preview: null });
    }
  },
}));

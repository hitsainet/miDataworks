// Purge (operator decision 2026-10-20): the latest plan of each archived item, keyed "kind:id",
// and the batch purge. Selection lives in the view (usePurgeSelection); this holds what the
// backend said.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { purgeApi } from '@/api/purge';
import type { PurgeBatch, PurgeItem, PurgeOutcome } from '@/types/purge';

export const purgeKey = (item: PurgeItem) => `${item.kind}:${item.id}`;

interface PurgeState {
  plans: Record<string, PurgeOutcome>;
  error: string | null;
  /** Ask what purging each item would remove (or why not); refreshes `plans`. */
  planPurge: (items: PurgeItem[]) => Promise<PurgeOutcome[]>;
  /** Purge the items; returns the per-item result, or null when the request itself failed. */
  purgeItems: (items: PurgeItem[], reason?: string) => Promise<PurgeBatch | null>;
}

const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export const usePurgeStore = create<PurgeState>()((set) => ({
  plans: {},
  error: null,
  planPurge: async (items) => {
    if (items.length === 0) return [];
    try {
      const out = await purgeApi.plan(items);
      set((s) => ({
        plans: { ...s.plans, ...Object.fromEntries(out.items.map((o) => [purgeKey(o), o])) },
        error: null,
      }));
      return out.items;
    } catch (e) {
      set({ error: message(e, 'Could not check what a permanent delete would remove.') });
      return [];
    }
  },
  purgeItems: async (items, reason) => {
    if (items.length === 0) return null;
    try {
      const out = await purgeApi.purge(items, reason);
      set((s) => {
        const plans = { ...s.plans };
        for (const o of out.items) {
          if (o.status === 'purged') delete plans[purgeKey(o)];
          else plans[purgeKey(o)] = o;
        }
        return { plans, error: null };
      });
      return out;
    } catch (e) {
      set({ error: message(e, 'Could not delete permanently.') });
      return null;
    }
  },
}));

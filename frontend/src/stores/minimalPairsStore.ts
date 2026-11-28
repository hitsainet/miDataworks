// Minimal-pair chains (009; operator decision 2026-10-07). The server advances a chain; the screen
// re-reads running chains on a timer and shows each stage, the stage a chain stopped at and why.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { minimalPairsApi } from '@/api/minimalPairs';
import type { ChainCreate, MinimalPairChain } from '@/types/minimalPairs';

export interface ChainRefusal {
  code: string;
  message: string;
}

interface MinimalPairsState {
  chains: MinimalPairChain[];
  refusal: ChainRefusal | null;
  error: string | null;
  /** `quiet`: the screen's own poll (hooks/useListPoll), which neither shows nor clears an error. */
  loadChains: (opts?: { quiet?: boolean }) => Promise<void>;
  startChain: (body: ChainCreate) => Promise<MinimalPairChain | null>;
  resumeChain: (id: string) => Promise<void>;
  cancelChain: (id: string) => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

function replace(list: MinimalPairChain[], chain: MinimalPairChain): MinimalPairChain[] {
  return list.some((c) => c.id === chain.id) ? list.map((c) => (c.id === chain.id ? chain : c)) : [chain, ...list];
}

export const useMinimalPairsStore = create<MinimalPairsState>((set, get) => ({
  chains: [],
  refusal: null,
  error: null,
  loadChains: async (opts) => {
    const quiet = opts?.quiet === true;
    try {
      const page = await minimalPairsApi.list();
      set(quiet ? { chains: page.items } : { chains: page.items, error: null });
    } catch (e) {
      if (!quiet) set({ error: message(e) });
    }
  },
  startChain: async (body) => {
    try {
      const chain = await minimalPairsApi.start(body);
      set({ chains: replace(get().chains, chain), refusal: null, error: null });
      return chain;
    } catch (e) {
      if (e instanceof ApiError) set({ refusal: { code: e.code, message: e.message } });
      else set({ error: message(e) });
      return null;
    }
  },
  resumeChain: async (id) => {
    try {
      const chain = await minimalPairsApi.resume(id);
      set({ chains: replace(get().chains, chain), error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  cancelChain: async (id) => {
    try {
      const chain = await minimalPairsApi.cancel(id);
      set({ chains: replace(get().chains, chain), error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
}));

// Feature 009 minimal-pair chains (operator decision 2026-10-07).
import { api, json } from '@/api/client';
import type { ChainCreate, MinimalPairChain } from '@/types/minimalPairs';

const chains = '/api/v1/minimal-pair-chains';
const enc = encodeURIComponent;

export const minimalPairsApi = {
  list: () => api<{ items: MinimalPairChain[]; total: number }>(`${chains}?limit=50`),
  get: (id: string) => api<MinimalPairChain>(`${chains}/${enc(id)}`),
  plan: (body: ChainCreate) => api<Record<string, unknown>>(`${chains}/plan`, json('POST', body)),
  start: (body: ChainCreate) => api<MinimalPairChain>(chains, json('POST', body)),
  resume: (id: string) => api<MinimalPairChain>(`${chains}/${enc(id)}/resume`, json('POST')),
  cancel: (id: string) => api<MinimalPairChain>(`${chains}/${enc(id)}/cancel`, json('POST')),
};

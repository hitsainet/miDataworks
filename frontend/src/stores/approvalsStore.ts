// Pending agent approvals for the banner (ADR-013). Polled; agents cannot decide (backend 403).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { approvalsApi } from '@/api/endpoints';
import type { Approval } from '@/types/api';

interface ApprovalsState {
  pending: Approval[];
  error: string | null;
  fetchPending: () => Promise<void>;
  approve: (id: string) => Promise<void>;
  reject: (id: string, reason: string) => Promise<void>;
}

export const useApprovalsStore = create<ApprovalsState>()((set, get) => ({
  pending: [],
  error: null,
  fetchPending: async () => {
    try {
      set({ pending: (await approvalsApi.pending()).approvals, error: null });
    } catch (error) {
      set({ error: error instanceof ApiError ? error.message : 'Could not load approvals.' });
    }
  },
  approve: async (id) => {
    try {
      await approvalsApi.approve(id);
    } catch (error) {
      set({ error: error instanceof ApiError ? error.message : 'Could not approve.' });
    }
    await get().fetchPending();
  },
  reject: async (id, reason) => {
    try {
      await approvalsApi.reject(id, reason);
    } catch (error) {
      set({ error: error instanceof ApiError ? error.message : 'Could not reject.' });
    }
    await get().fetchPending();
  },
}));

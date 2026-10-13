// Pending agent approvals for the banner (ADR-013). Polled; agents cannot decide (backend 403).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { approvalsApi } from '@/api/endpoints';
import type { Approval } from '@/types/api';

interface ApprovalsState {
  pending: Approval[];
  /** A failed decision (expired, already decided): kept until the next decision. */
  error: string | null;
  /** A failed list load: cleared by the next successful load. */
  loadError: string | null;
  fetchPending: () => Promise<void>;
  approve: (id: string) => Promise<void>;
  reject: (id: string, reason: string) => Promise<void>;
}

/** The operator-facing sentence for a refused decision: what happened and what to do next. */
export function decisionError(error: unknown, verb: string): string {
  if (error instanceof ApiError) {
    if (error.details?.status === 'expired') return 'This request expired. Ask the agent to try again.';
    if (error.status === 409) return `${error.message} The list has been refreshed.`;
    return error.message;
  }
  return `Could not ${verb}.`;
}

export const useApprovalsStore = create<ApprovalsState>()((set, get) => ({
  pending: [],
  error: null,
  loadError: null,
  fetchPending: async () => {
    try {
      set({ pending: (await approvalsApi.pending()).approvals, loadError: null });
    } catch (error) {
      set({ loadError: error instanceof ApiError ? error.message : 'Could not load approvals.' });
    }
  },
  approve: async (id) => {
    set({ error: null });
    try {
      await approvalsApi.approve(id);
    } catch (error) {
      set({ error: decisionError(error, 'approve') });
      await get().fetchPending();
      return;
    }
    await get().fetchPending();
  },
  reject: async (id, reason) => {
    set({ error: null });
    try {
      await approvalsApi.reject(id, reason);
    } catch (error) {
      set({ error: decisionError(error, 'reject') });
      await get().fetchPending();
      return;
    }
    await get().fetchPending();
  },
}));

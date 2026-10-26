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
  /** Decided requests (everything not pending), newest first, for the Settings history card. */
  history: Approval[];
  historyIncludeArchived: boolean;
  historyError: string | null;
  fetchHistory: () => Promise<void>;
  setHistoryIncludeArchived: (value: boolean) => Promise<void>;
  archiveApproval: (id: string) => Promise<void>;
  unarchiveApproval: (id: string) => Promise<void>;
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
  history: [],
  historyIncludeArchived: false,
  historyError: null,
  fetchHistory: async () => {
    try {
      const { approvals } = await approvalsApi.all(get().historyIncludeArchived);
      set({ history: approvals.filter((a) => a.status !== 'pending'), historyError: null });
    } catch (error) {
      set({ historyError: error instanceof ApiError ? error.message : 'Could not load earlier requests.' });
    }
  },
  setHistoryIncludeArchived: async (value) => {
    set({ historyIncludeArchived: value });
    await get().fetchHistory();
  },
  archiveApproval: async (id) => {
    try {
      await approvalsApi.archive(id);
    } catch (error) {
      set({ historyError: error instanceof ApiError ? error.message : 'Could not archive.' });
      return;
    }
    await get().fetchHistory();
  },
  unarchiveApproval: async (id) => {
    try {
      await approvalsApi.unarchive(id);
    } catch (error) {
      set({ historyError: error instanceof ApiError ? error.message : 'Could not unarchive.' });
      return;
    }
    await get().fetchHistory();
  },
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

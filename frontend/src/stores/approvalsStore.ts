// Agent approvals (ADR-013; ENH-008 D6-D7): the one store behind the banner, the Approvals screen
// and the menu badge. The banner owns the poll of the waiting list; agents cannot decide (backend 403).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { approvalsApi } from '@/api/endpoints';
import type { Approval, ApproveBatchResult, BatchKind } from '@/types/api';

/** History is read this many at a time; Show older reads the next page (ENH-008 D6). */
export const HISTORY_PAGE = 200;

/** Sent when the operator rejects without typing a reason (ENH-008 D8c; the API requires one). */
export const DEFAULT_REJECT_REASON = 'Rejected by the operator.';

interface ApprovalsState {
  pending: Approval[];
  /** A failed decision (expired, already decided): kept until the next decision. */
  error: string | null;
  /** The request the failed decision was for, so its card can show the error in place. */
  errorId: string | null;
  /** A failed list load: cleared by the next successful load. */
  loadError: string | null;
  fetchPending: () => Promise<void>;
  /** Decided requests (everything not pending), the most recent decision first. */
  history: Approval[];
  /** The last page read was full, so there may be older ones (Show older). */
  historyHasOlder: boolean;
  historyIncludeArchived: boolean;
  historyError: string | null;
  /** Re-reads the newest page, keeping any older pages already shown. */
  fetchHistory: () => Promise<void>;
  fetchOlderHistory: () => Promise<void>;
  setHistoryIncludeArchived: (value: boolean) => Promise<void>;
  archiveApproval: (id: string) => Promise<void>;
  unarchiveApproval: (id: string) => Promise<void>;
  /** True when the decision went through. */
  approve: (id: string) => Promise<boolean>;
  reject: (id: string, reason: string) => Promise<boolean>;
  /** ENH-006 D79/D81: the outcome of the last "Approve all …" and its kind, or its refusal. */
  batchKind: BatchKind | null;
  batchNotice: string | null;
  batchError: string | null;
  /** Approves every listed request of one kind in one decision: the server's answer, or null when refused. */
  approveBatch: (kind: BatchKind, ids: string[]) => Promise<ApproveBatchResult | null>;
}

/** The waiting requests "Approve all settings changes" covers: the server marks each one. */
export function settingsChanges(pending: Approval[]): Approval[] {
  return pending.filter((a) => a.settings_change === true);
}

/** The waiting requests "Approve all code rules" covers (ENH-006 D81): an agent's code-rule save. */
export function codeRuleRequests(pending: Approval[]): Approval[] {
  return pending.filter((a) => a.action === 'code_rule_save');
}

export const BATCH_NOUN: Record<BatchKind, [string, string]> = {
  settings: ['settings change', 'settings changes'],
  code_rules: ['code rule', 'code rules'],
};

/** One line on what a batch did: how many ran, and each that did not, with its reason. */
export function batchSummary(result: ApproveBatchResult, kind: BatchKind = 'settings'): string {
  const ran = result.results.filter((r) => r.status === 'executed').length;
  const others = result.results.filter((r) => r.status !== 'executed');
  const noun = BATCH_NOUN[kind][result.results.length === 1 ? 0 : 1];
  const head = `Approved ${ran} of ${result.results.length} ${noun}.`;
  if (others.length === 0) return head;
  return `${head} Not done: ${others
    .map((r) => `${r.approval_id} (${r.status}${r.error?.message ? `: ${r.error.message}` : ''})`)
    .join('; ')}.`;
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

/** Soonest to expire first (ENH-008 D6): the order of the waiting cards everywhere. */
export function bySoonestExpiry(a: Approval, b: Approval): number {
  const ta = Date.parse(a.expires_at);
  const tb = Date.parse(b.expires_at);
  return (Number.isFinite(ta) ? ta : Infinity) - (Number.isFinite(tb) ? tb : Infinity);
}

export const useApprovalsStore = create<ApprovalsState>()((set, get) => {
  const historyPage = async (offset: number) =>
    (await approvalsApi.history(get().historyIncludeArchived, offset, HISTORY_PAGE)).approvals.filter(
      (a) => a.status !== 'pending',
    );

  const decide = async (id: string, verb: string, call: () => Promise<unknown>): Promise<boolean> => {
    set({ error: null, errorId: null });
    try {
      await call();
    } catch (error) {
      set({ error: decisionError(error, verb), errorId: id });
      await get().fetchPending();
      return false;
    }
    await get().fetchPending();
    return true;
  };

  return {
    pending: [],
    error: null,
    errorId: null,
    loadError: null,
    history: [],
    historyHasOlder: false,
    historyIncludeArchived: false,
    historyError: null,
    fetchHistory: async () => {
      try {
        const page = await historyPage(0);
        const ids = new Set(page.map((a) => a.id));
        const older = get().history.length > HISTORY_PAGE ? get().history.slice(HISTORY_PAGE).filter((a) => !ids.has(a.id)) : [];
        set({
          history: [...page, ...older],
          historyHasOlder: older.length > 0 ? get().historyHasOlder : page.length === HISTORY_PAGE,
          historyError: null,
        });
      } catch (error) {
        set({ historyError: error instanceof ApiError ? error.message : 'Could not load the history.' });
      }
    },
    fetchOlderHistory: async () => {
      try {
        const page = await historyPage(get().history.length);
        const ids = new Set(get().history.map((a) => a.id));
        set({
          history: [...get().history, ...page.filter((a) => !ids.has(a.id))],
          historyHasOlder: page.length === HISTORY_PAGE,
          historyError: null,
        });
      } catch (error) {
        set({ historyError: error instanceof ApiError ? error.message : 'Could not load older requests.' });
      }
    },
    setHistoryIncludeArchived: async (value) => {
      set({ historyIncludeArchived: value, history: [], historyHasOlder: false });
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
        const { approvals } = await approvalsApi.pending();
        set({ pending: [...approvals].sort(bySoonestExpiry), loadError: null });
      } catch (error) {
        set({ loadError: error instanceof ApiError ? error.message : 'Could not load approvals.' });
      }
    },
    batchKind: null,
    batchNotice: null,
    batchError: null,
    approveBatch: async (kind, ids) => {
      set({ batchKind: kind, batchNotice: null, batchError: null, error: null, errorId: null });
      try {
        const result = await approvalsApi.approveBatch(kind, ids);
        set({ batchNotice: batchSummary(result, kind) });
        return result;
      } catch (error) {
        set({ batchError: decisionError(error, `approve the ${BATCH_NOUN[kind][1]}`) });
        return null;
      } finally {
        await get().fetchPending();
      }
    },
    approve: (id) => decide(id, 'approve', () => approvalsApi.approve(id)),
    reject: (id, reason) => decide(id, 'reject', () => approvalsApi.reject(id, reason.trim() || DEFAULT_REJECT_REASON)),
  };
});

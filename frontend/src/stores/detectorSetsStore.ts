// Detector sets (FTDD 009 section 7.1). Server state is authoritative: every mutation is followed by
// a re-read. Send progress arrives on `dataworks/detector-sends/{id}` (hooks/useDetectorSend.ts),
// with polling of GET /detector-sends/{id} while the socket is down (ADR-008).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { detectorSetsApi } from '@/api/detectorSets';
import type {
  ChecksResult,
  DetectorSet,
  DetectorSetSummary,
  ResultsSnapshot,
  RoleIn,
  SendDetail,
} from '@/types/detectorSets';

interface DetectorSetsState {
  sets: DetectorSetSummary[];
  current: DetectorSet | null;
  checks: ChecksResult | null;
  send: SendDetail | null;
  results: ResultsSnapshot | null;
  pendingApproval: string | null;
  loading: boolean;
  error: string | null;
  notice: string | null;
  fetchSets: () => Promise<void>;
  open: (id: string) => Promise<void>;
  close: () => void;
  createSet: (body: { name: string; description?: string; positive_meaning?: string; roles: RoleIn[] }) => Promise<DetectorSet | null>;
  updateRoles: (roles: RoleIn[]) => Promise<boolean>;
  runChecks: () => Promise<void>;
  startSend: (body: { namespace?: string | null; repositories?: Record<string, string>; visibility: 'private' | 'public' }) => Promise<boolean>;
  fetchSend: (sendId: string) => Promise<void>;
  resumeSend: () => Promise<void>;
  cancelSend: () => Promise<void>;
  refreshResults: () => Promise<void>;
  fetchResults: () => Promise<void>;
  archive: () => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export const useDetectorSetsStore = create<DetectorSetsState>((set, get) => ({
  sets: [],
  current: null,
  checks: null,
  send: null,
  results: null,
  pendingApproval: null,
  loading: false,
  error: null,
  notice: null,
  fetchSets: async () => {
    try {
      const page = await detectorSetsApi.list();
      set({ sets: page.items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  open: async (id) => {
    set({ loading: true, checks: null, send: null, results: null, notice: null });
    try {
      const current = await detectorSetsApi.get(id);
      set({ current, error: null });
      const last = current.sends?.[0];
      if (last) await get().fetchSend(last.id);
    } catch (e) {
      set({ error: message(e) });
    } finally {
      set({ loading: false });
    }
  },
  close: () => set({ current: null, checks: null, send: null, results: null, notice: null, pendingApproval: null }),
  createSet: async (body) => {
    try {
      const created = await detectorSetsApi.create(body);
      await get().fetchSets();
      set({ current: created, error: null });
      return created;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  updateRoles: async (roles) => {
    const current = get().current;
    if (!current) return false;
    try {
      const updated = await detectorSetsApi.update(current.id, { roles });
      set({ current: updated, checks: null, error: null });
      return true;
    } catch (e) {
      set({ error: message(e) });
      return false;
    }
  },
  runChecks: async () => {
    const current = get().current;
    if (!current) return;
    try {
      set({ checks: await detectorSetsApi.checks(current.id), error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  startSend: async (body) => {
    const current = get().current;
    if (!current) return false;
    try {
      const started = await detectorSetsApi.send(current.id, body);
      if (started.approval_id) {
        set({ pendingApproval: started.approval_id, notice: 'Waiting for the operator to approve this send.' });
      } else if (started.send_id) {
        await get().fetchSend(started.send_id);
        set({ notice: null });
      }
      set({ error: null });
      return true;
    } catch (e) {
      set({ error: message(e) });
      return false;
    }
  },
  fetchSend: async (sendId) => {
    try {
      set({ send: await detectorSetsApi.sendDetail(sendId) });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  resumeSend: async () => {
    const send = get().send;
    if (!send) return;
    try {
      await detectorSetsApi.resume(send.id);
      await get().fetchSend(send.id);
    } catch (e) {
      set({ error: message(e) });
    }
  },
  cancelSend: async () => {
    const send = get().send;
    if (!send) return;
    try {
      await detectorSetsApi.cancel(send.id);
      await get().fetchSend(send.id);
    } catch (e) {
      set({ error: message(e) });
    }
  },
  refreshResults: async () => {
    const current = get().current;
    if (!current) return;
    try {
      set({ results: await detectorSetsApi.refresh(current.id), error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  fetchResults: async () => {
    const current = get().current;
    if (!current) return;
    try {
      set({ results: await detectorSetsApi.results(current.id) });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) set({ error: message(e) });
    }
  },
  archive: async () => {
    const current = get().current;
    if (!current) return;
    try {
      set({ current: await detectorSetsApi.archive(current.id) });
      await get().fetchSets();
    } catch (e) {
      set({ error: message(e) });
    }
  },
}));

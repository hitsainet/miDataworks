// Review (FTDD 006 section 7.2): queues, the open queue's items (paged), decision histories, and the
// audit of a version. A decision is appended OPTIMISTICALLY and rolled back with a toast when the
// API refuses it; the server's decision then replaces the optimistic one.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { reviewApi } from '@/api/review';
import type { AuditStatus, Decision, DecisionIn, QueueCreate, ReviewItem, ReviewQueue } from '@/types/review';

interface ReviewState {
  queues: ReviewQueue[];
  selectedQueueId: string | null;
  items: ReviewItem[];
  total: number;
  page: number;
  cursor: number;
  history: Record<string, Decision[]>;
  audits: Record<string, AuditStatus>;
  error: string | null;
  fetchQueues: () => Promise<void>;
  selectQueue: (id: string | null) => Promise<void>;
  fetchItems: (page?: number) => Promise<void>;
  move: (delta: number) => void;
  decide: (itemId: string, body: DecisionIn, who: string) => Promise<boolean>;
  fetchHistory: (itemId: string) => Promise<void>;
  createQueue: (body: QueueCreate) => Promise<ReviewQueue | null>;
  fetchAudit: (versionId: string) => Promise<void>;
  drawAudit: (versionId: string, size: number) => Promise<AuditStatus | null>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
export const PAGE_SIZE = 50;

export const useReviewStore = create<ReviewState>((set, get) => ({
  queues: [],
  selectedQueueId: null,
  items: [],
  total: 0,
  page: 1,
  cursor: 0,
  history: {},
  audits: {},
  error: null,
  fetchQueues: async () => {
    try {
      const page = await reviewApi.queues();
      set({ queues: page.items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  selectQueue: async (id) => {
    set({ selectedQueueId: id, items: [], total: 0, page: 1, cursor: 0 });
    if (id) await get().fetchItems(1);
  },
  fetchItems: async (page = 1) => {
    const id = get().selectedQueueId;
    if (!id) return;
    try {
      const result = await reviewApi.items(id, page, PAGE_SIZE);
      set({ items: result.items, total: result.total, page, cursor: 0, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  move: (delta) => set((s) => ({ cursor: Math.max(0, Math.min(s.items.length - 1, s.cursor + delta)) })),
  decide: async (itemId, body, who) => {
    const before = get().items;
    const item = before.find((i) => i.id === itemId);
    if (!item) return false;
    const optimistic: Decision = {
      id: `pending-${itemId}`,
      item_id: itemId,
      queue_id: item.queue_id,
      row_key: item.row_key,
      decision: body.decision,
      override_label: body.override_label ?? null,
      reason: body.reason ?? '',
      decided_by: who,
      decided_by_origin: 'operator',
      model_output_visible: !item.model_output_hidden,
      version_id: null,
      created_at: new Date().toISOString(),
    };
    set({ items: before.map((i) => (i.id === itemId ? { ...i, latest_decision: optimistic, pending: true } : i)) });
    try {
      const saved = await reviewApi.decide(itemId, body);
      set((s) => ({
        items: s.items.map((i) => (i.id === itemId ? { ...i, latest_decision: saved, pending: false } : i)),
        history: { ...s.history, [itemId]: [...(s.history[itemId] ?? []), saved] },
        queues: s.queues.map((q) => (q.id === item.queue_id && !item.latest_decision ? { ...q, decided: q.decided + 1 } : q)),
        error: null,
      }));
      return true;
    } catch (e) {
      set({ items: before, error: message(e) });
      return false;
    }
  },
  fetchHistory: async (itemId) => {
    try {
      const decisions = await reviewApi.history(itemId);
      set((s) => ({ history: { ...s.history, [itemId]: decisions } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  createQueue: async (body) => {
    try {
      const queue = await reviewApi.createQueue(body);
      set((s) => ({ queues: [queue, ...s.queues], error: null }));
      return queue;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  fetchAudit: async (versionId) => {
    try {
      const status = await reviewApi.audit(versionId);
      set((s) => ({ audits: { ...s.audits, [versionId]: status } }));
    } catch (e) {
      set({ error: message(e) });
    }
  },
  drawAudit: async (versionId, size) => {
    try {
      const status = await reviewApi.drawAudit(versionId, size);
      set((s) => ({ audits: { ...s.audits, [versionId]: status }, error: null }));
      await get().fetchQueues();
      return status;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
}));

// Review (FTDD 006 section 7.2): queues, the open queue's items (paged), decision histories, and the
// audit of a version. A decision is appended OPTIMISTICALLY and rolled back with a toast when the
// API refuses it; the server's decision then replaces the optimistic one.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { reviewApi } from '@/api/review';
import { safeStorage } from '@/stores/safeStorage';
import type { AuditStatus, Decision, DecisionIn, QueueCreate, ReviewItem, ReviewQueue } from '@/types/review';

interface ReviewState {
  queues: ReviewQueue[];
  selectedQueueId: string | null;
  items: ReviewItem[];
  total: number;
  page: number;
  cursor: number;
  /** A page is loading: the row on screen takes no decision, or a fast key would land on the wrong row. */
  paging: boolean;
  history: Record<string, Decision[]>;
  audits: Record<string, AuditStatus>;
  error: string | null;
  /** Closed queues are hidden unless the reviewer asks for them. */
  showClosed: boolean;
  setShowClosed: (show: boolean) => Promise<void>;
  fetchQueues: () => Promise<void>;
  closeQueue: (id: string, reason?: string) => Promise<ReviewQueue | null>;
  selectQueue: (id: string | null) => Promise<void>;
  /** Opens the queue this browser had open, at the same row, unless one is open already. */
  restoreOpenQueue: () => Promise<void>;
  fetchItems: (page?: number, at?: 'first' | 'last' | number) => Promise<void>;
  move: (delta: number) => void;
  /** Goes to a row by its number in the queue, from 1. False, and nothing moves, when there is no such row. */
  goToRow: (row: number) => boolean;
  decide: (itemId: string, body: DecisionIn, who: string) => Promise<boolean>;
  fetchHistory: (itemId: string) => Promise<void>;
  createQueue: (body: QueueCreate) => Promise<ReviewQueue | null>;
  fetchAudit: (versionId: string) => Promise<void>;
  drawAudit: (versionId: string, size: number) => Promise<AuditStatus | null>;
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
export const PAGE_SIZE = 50;

// The open queue and row survive a refresh (operator, 2026-11-01): a long labelling session must not
// lose its place. Only ids and a position; the rows come from the API.
const OPEN_QUEUE_KEY = 'midataworks-review-open-queue';

function rememberOpen(id: string | null, page = 1, cursor = 0): void {
  if (id) void safeStorage.setItem(OPEN_QUEUE_KEY, JSON.stringify({ id, page, cursor }));
  else void safeStorage.removeItem(OPEN_QUEUE_KEY);
}

function rememberedOpen(): { id: string; page: number; cursor: number } | null {
  try {
    const { id, page, cursor } = JSON.parse(String(safeStorage.getItem(OPEN_QUEUE_KEY))) as Record<string, unknown>;
    if (typeof id === 'string' && Number.isInteger(page) && (page as number) >= 1 && Number.isInteger(cursor) && (cursor as number) >= 0) {
      return { id, page: page as number, cursor: cursor as number };
    }
  } catch {
    // nothing remembered, or not something this version wrote
  }
  return null;
}

export const useReviewStore = create<ReviewState>((set, get) => ({
  queues: [],
  selectedQueueId: null,
  items: [],
  total: 0,
  page: 1,
  cursor: 0,
  paging: false,
  history: {},
  audits: {},
  error: null,
  showClosed: false,
  setShowClosed: async (show) => {
    set({ showClosed: show });
    await get().fetchQueues();
  },
  fetchQueues: async () => {
    try {
      const page = await reviewApi.queues(get().showClosed);
      set({ queues: page.items, error: null });
    } catch (e) {
      set({ error: message(e) });
    }
  },
  closeQueue: async (id, reason) => {
    try {
      const closed = await reviewApi.closeQueue(id, reason);
      if (get().selectedQueueId === id && !get().showClosed) rememberOpen(null);
      set((s) => ({
        queues: s.showClosed ? s.queues.map((q) => (q.id === id ? closed : q)) : s.queues.filter((q) => q.id !== id),
        ...(s.selectedQueueId === id && !s.showClosed ? { selectedQueueId: null, items: [], total: 0, page: 1, cursor: 0 } : {}),
        error: null,
      }));
      return closed;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  selectQueue: async (id) => {
    rememberOpen(id);
    set({ selectedQueueId: id, items: [], total: 0, page: 1, cursor: 0 });
    // Decisions made in another tab or by an agent: the count on the card must not read 0 beside a decided row.
    if (id) await Promise.all([get().fetchItems(1), get().fetchQueues()]);
  },
  restoreOpenQueue: async () => {
    if (get().selectedQueueId || get().error) return;
    const open = rememberedOpen();
    if (!open) return;
    if (!get().queues.some((q) => q.id === open.id)) {
      // closed since, and closed queues are not listed: there is nothing to come back to
      rememberOpen(null);
      return;
    }
    set({ selectedQueueId: open.id, items: [], total: 0, page: open.page, cursor: 0 });
    await get().fetchItems(open.page, open.cursor);
    if (get().selectedQueueId === open.id && !get().error && get().items.length === 0 && open.page > 1) await get().fetchItems(1);
  },
  fetchItems: async (page = 1, at = 'first') => {
    const id = get().selectedQueueId;
    if (!id) return;
    set({ paging: true });
    try {
      const result = await reviewApi.items(id, page, PAGE_SIZE);
      if (get().selectedQueueId !== id) return;
      const last = Math.max(0, result.items.length - 1);
      const cursor = at === 'last' ? last : at === 'first' ? 0 : Math.min(at, last);
      rememberOpen(id, page, cursor);
      set({ items: result.items, total: result.total, page, cursor, error: null });
    } catch (e) {
      set({ error: message(e) });
    } finally {
      set({ paging: false });
    }
  },
  // Moving off either end of a page goes on to the next one: a queue is one list to its reviewer, and
  // stopping at row 50 with nothing said read as a broken queue (dry run, 2026-11-01).
  move: (delta) => {
    const { selectedQueueId, cursor, items, page, total, paging, fetchItems } = get();
    if (paging) return;
    const next = cursor + delta;
    if (next >= items.length && page * PAGE_SIZE < total) return void fetchItems(page + 1);
    if (next < 0 && page > 1) return void fetchItems(page - 1, 'last');
    const moved = Math.max(0, Math.min(items.length - 1, next));
    rememberOpen(selectedQueueId, page, moved);
    set({ cursor: moved });
  },
  goToRow: (row) => {
    const { selectedQueueId, page, total, paging, fetchItems } = get();
    if (paging || !Number.isInteger(row) || row < 1 || row > total) return false;
    const target = Math.ceil(row / PAGE_SIZE);
    const cursor = (row - 1) % PAGE_SIZE;
    if (target !== page) {
      void fetchItems(target, cursor);
      return true;
    }
    rememberOpen(selectedQueueId, page, cursor);
    set({ cursor });
    return true;
  },
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

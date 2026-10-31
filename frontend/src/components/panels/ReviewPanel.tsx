// Review (FR-006.34; ADR-020 suite shell): queues on the left, the current item on the right.
// Keyboard: a accept, o override, f flag, r reject (external only), j next, k previous — ignored
// while typing in a field. Decisions are optimistic and roll back with an error when refused.
import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

import { CloseButton, toggleOpen, useEscapeToClose } from '@/components/common/DetailPopup';
import { PageHead } from '@/components/layout/PageHead';
import { AuditBanner } from '@/components/review/AuditBanner';
import { CreateQueueDialog } from '@/components/review/CreateQueueDialog';
import { DecisionBar } from '@/components/review/DecisionBar';
import { DecisionHistory } from '@/components/review/DecisionHistory';
import { DrawAuditDialog } from '@/components/review/DrawAuditDialog';
import { ItemView } from '@/components/review/ItemView';
import { QueueList } from '@/components/review/QueueList';
import type { PanelDef } from '@/config/panels';
import { useReviewStore } from '@/stores/reviewStore';
import { useSettingsStore } from '@/stores/settingsStore';
import type { DecisionIn } from '@/types/review';

export const SHORTCUTS = ['a', 'o', 'f', 'r'] as const;

export function ReviewPanel({ panel }: { panel: PanelDef }) {
  const queues = useReviewStore((s) => s.queues);
  const selectedId = useReviewStore((s) => s.selectedQueueId);
  const items = useReviewStore((s) => s.items);
  const total = useReviewStore((s) => s.total);
  const page = useReviewStore((s) => s.page);
  const cursor = useReviewStore((s) => s.cursor);
  const history = useReviewStore((s) => s.history);
  const audits = useReviewStore((s) => s.audits);
  const error = useReviewStore((s) => s.error);
  const fetchQueues = useReviewStore((s) => s.fetchQueues);
  const selectQueue = useReviewStore((s) => s.selectQueue);
  const fetchItems = useReviewStore((s) => s.fetchItems);
  const move = useReviewStore((s) => s.move);
  const decide = useReviewStore((s) => s.decide);
  const fetchHistory = useReviewStore((s) => s.fetchHistory);
  const fetchAudit = useReviewStore((s) => s.fetchAudit);
  const showClosed = useReviewStore((s) => s.showClosed);
  const setShowClosed = useReviewStore((s) => s.setShowClosed);
  const closeQueue = useReviewStore((s) => s.closeQueue);
  const operator = useSettingsStore((s) => s.settings.find((x) => x.key === 'operator_name')?.value ?? '');
  const [creating, setCreating] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => void fetchQueues(), [fetchQueues]);
  const queue = queues.find((q) => q.id === selectedId);
  const closeView = () => void selectQueue(null);
  useEscapeToClose(Boolean(queue), closeView);
  const item = items[cursor];
  useEffect(() => {
    if (item) void fetchHistory(item.id);
  }, [item, fetchHistory]);
  useEffect(() => {
    if (queue?.kind === 'audit' && queue.version_id) void fetchAudit(queue.version_id);
  }, [queue, fetchAudit]);

  const onDecide = useCallback(
    async (body: DecisionIn) => {
      if (!item) return;
      if (await decide(item.id, body, operator || 'you')) {
        if (queue?.kind === 'audit' && queue.version_id) void fetchAudit(queue.version_id);
        move(1);
      }
    },
    [item, decide, operator, queue, fetchAudit, move],
  );

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'j') return move(1);
    if (e.key === 'k') return move(-1);
    if ((SHORTCUTS as readonly string[]).includes(e.key)) {
      const button = barRef.current?.querySelector<HTMLButtonElement>(`button[data-key="${e.key}"]`);
      if (button && !button.disabled) button.click();
    }
  };

  return (
    <div onKeyDown={onKey} tabIndex={-1} className="outline-hidden" data-testid="review-panel">
      <PageHead help="review" title={panel.title} subtitle={panel.subtitle} />
      <details className="mb-4 text-sm text-slate-600 dark:text-slate-300" data-testid="review-help">
        <summary className="cursor-pointer">How review works</summary>
        <p className="mt-2">
          Your decisions are kept with who made them and why; a later decision on the same row replaces the earlier one
          in exports, and nothing is ever edited. Only your override changes a label; an agent may accept or flag a row,
          never override it. In calibration-labeling queues the model's answer is hidden so it cannot anchor yours. An
          audit is complete when you have decided every sampled row; a public push needs one.
        </p>
      </details>
      {error && <div role="alert" className="mb-4 rounded-card border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="mb-4 flex gap-2">
        <button type="button" className="rounded-control bg-indigo-500 px-3 py-1.5 text-sm text-white focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => setCreating(true)}>Create a review queue</button>
        <button type="button" className="rounded-control border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => setAuditing(true)}>Draw an audit</button>
        <label className="ml-auto flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={showClosed} onChange={(e) => void setShowClosed(e.target.checked)} data-testid="show-closed" className="focus-visible:ring-2 focus-visible:ring-indigo-500" />
          Show closed queues
        </label>
      </div>
      {creating && <CreateQueueDialog onClose={() => setCreating(false)} />}
      {auditing && <DrawAuditDialog onClose={() => setAuditing(false)} />}
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="lg:col-span-2">
          {/* The open queue's card closes it; another card switches (operator, 2026-10-31). */}
          <QueueList queues={queues} selectedId={selectedId} onOpen={(id) => void selectQueue(toggleOpen(selectedId, id))} />
        </div>
        <div className="lg:col-span-3 min-w-0 stack-y-4">
          {queue && (
            <div className="flex items-start gap-2" data-testid="queue-view-head">
              <p className="text-sm font-semibold min-w-0">{queue.question}</p>
              <CloseButton onClose={closeView} what="the queue view (the queue itself stays as it is)" className="ml-auto" />
            </div>
          )}
          {queue?.kind === 'audit' && queue.version_id && audits[queue.version_id] && <AuditBanner status={audits[queue.version_id]} />}
          {queue && queue.state === 'open' && queue.kind !== 'audit' && (
            <div className="flex justify-end">
              <button type="button" data-testid="close-queue" className="rounded-control border border-slate-300 dark:border-slate-600 px-3 py-1 text-xs focus-visible:ring-2 focus-visible:ring-indigo-500"
                onClick={() => {
                  if (window.confirm('Close this queue? It leaves the list and takes no more decisions; its decisions are kept.')) void closeQueue(queue.id);
                }}>
                Close this queue
              </button>
            </div>
          )}
          {queue?.state === 'closed' && (
            <p role="note" data-testid="queue-closed-note" className="rounded-control border border-slate-200 dark:border-slate-700 px-3 py-2 text-xs text-slate-600 dark:text-slate-300">
              This queue is closed{queue.closed_by ? ` by ${queue.closed_by}` : ''}{queue.close_reason ? ` (${queue.close_reason})` : ''}. Its decisions are kept; it takes no new ones.
            </p>
          )}
          {!queue ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">Open a queue to review its rows.</p>
          ) : !item ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">This queue has no items on this page.</p>
          ) : (
            <>
              <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                <span data-testid="item-position">Row {(page - 1) * 50 + cursor + 1} of {total}</span>
                <span className="flex gap-2">
                  <button type="button" className="underline rounded-control focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => move(-1)}>Previous row (k)</button>
                  <button type="button" className="underline rounded-control focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => move(1)}>Next row (j)</button>
                  {page * 50 < total && <button type="button" className="underline rounded-control focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => void fetchItems(page + 1)}>Next page</button>}
                </span>
              </div>
              <ItemView item={item} queue={queue} />
              {queue.state === 'open' && <div ref={barRef}>
                <DecisionBar key={item.id} kind={queue.kind} labels={queue.label_set} hidden={item.model_output_hidden} pending={item.pending} onDecide={(b) => void onDecide(b)} />
              </div>}
              <DecisionHistory decisions={history[item.id] ?? []} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

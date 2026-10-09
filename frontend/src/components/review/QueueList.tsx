// Queues with kind, size, progress and creator (FR-006.34). miForge queues carry an orange chip.
import type { ReviewQueue } from '@/types/review';

import { DecidedBy } from './DecidedBy';

const KIND: Record<ReviewQueue['kind'], string> = {
  label_review: 'Label review',
  calibration_labeling: 'Calibration labeling',
  audit: 'Audit',
  external: 'External candidates',
};

export function QueueList({ queues, selectedId, onOpen }: { queues: ReviewQueue[]; selectedId: string | null; onOpen: (id: string) => void }) {
  if (queues.length === 0) return <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-queues">No review queues yet. Create one, or draw an audit for a version.</p>;
  return (
    <ul className="space-y-2" data-testid="queue-list">
      {queues.map((q) => (
        <li key={q.id}>
          <button type="button" onClick={() => onOpen(q.id)} data-testid="queue-card" aria-current={q.id === selectedId}
            className={`w-full rounded-lg border p-3 text-left text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 ${q.id === selectedId ? 'border-indigo-400 bg-indigo-50 dark:bg-indigo-500/10' : 'border-slate-200 dark:border-slate-700'}`}>
            <span className="flex items-center justify-between gap-2">
              <span className="font-medium">{KIND[q.kind]}</span>
              {q.origin_app === 'miforge' && <span className="rounded bg-orange-100 dark:bg-orange-500/15 px-1.5 text-[11px] text-orange-700 dark:text-orange-300" data-testid="miforge-chip">miForge</span>}
              <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400">{q.decided} of {q.items} decided{q.state === 'closed' ? ' · closed' : ''}</span>
            </span>
            <span className="mt-1 block truncate text-xs text-slate-600 dark:text-slate-300">{q.question}</span>
            <span className="mt-1 block text-[11px] text-slate-500">Created by <DecidedBy who={q.created_by} origin={q.created_by_origin} /></span>
          </button>
        </li>
      ))}
    </ul>
  );
}

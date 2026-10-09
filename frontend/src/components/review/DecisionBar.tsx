// Buttons that name their action (FR-006.34): Accept label, Override label, Flag row, and Reject
// candidate on external queues only (T-29). A reason is required for override, flag and reject.
// Keyboard: a, o, f, r (handled by the Review screen).
import { useState } from 'react';

import type { DecisionIn, QueueKind } from '@/types/review';

export function DecisionBar({ kind, labels, onDecide, hidden, pending }: {
  kind: QueueKind;
  labels: string[];
  onDecide: (body: DecisionIn) => void;
  hidden: boolean;
  pending?: boolean;
}) {
  const [reason, setReason] = useState('');
  const [label, setLabel] = useState(labels[0] ?? '');
  const needsReason = !reason.trim();
  const btn = 'rounded-md border px-3 py-1.5 text-sm disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500';
  const assignOnly = kind === 'calibration_labeling';
  return (
    <div className="space-y-2" data-testid="decision-bar">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs">
          Label{' '}
          <select aria-label="Override label" value={label} onChange={(e) => setLabel(e.target.value)} className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-1 py-0.5">
            {labels.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <input aria-label="Reason" placeholder="Reason (required to override, flag or reject)" value={reason} onChange={(e) => setReason(e.target.value)}
          className="min-w-[16rem] flex-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" />
      </div>
      <div className="flex flex-wrap gap-2">
        {!(assignOnly && hidden) && (
          <button type="button" data-key="a" disabled={pending} className={`${btn} border-green-400 text-green-800 dark:text-green-300`} onClick={() => onDecide({ decision: 'accept', reason: reason || undefined })}>
            Accept label
          </button>
        )}
        <button type="button" data-key="o" disabled={pending || (!assignOnly && needsReason)} className={`${btn} border-indigo-400`}
          onClick={() => onDecide({ decision: 'override', override_label: label, reason: reason || undefined })}>
          {assignOnly ? 'Assign label' : 'Override label'}
        </button>
        <button type="button" data-key="f" disabled={pending || needsReason} className={`${btn} border-amber-400`} onClick={() => onDecide({ decision: 'flag', reason })}>
          Flag row
        </button>
        {kind === 'external' && (
          <button type="button" data-key="r" disabled={pending || needsReason} className={`${btn} border-red-400`} onClick={() => onDecide({ decision: 'reject', reason })}>
            Reject candidate
          </button>
        )}
      </div>
    </div>
  );
}

// Buttons that name their action (FR-006.34): Accept label, Override label, Flag row, and Reject
// candidate on external queues only (T-29). A reason is required for override, flag and reject.
// Keyboard: a, o, f, r (handled by the Review screen). Labels are side-by-side buttons, however many
// there are, and at most one reads as chosen: the row's current answer. Nothing is chosen in advance:
// a pre-selected first label recorded "yes" on any press of o with no choice made. On a
// calibration-labeling queue a label's button assigns it at once, keys 1..9 in the label set's order.
import { useState } from 'react';

import type { DecisionIn, QueueKind } from '@/types/review';

/** Labels past this many are buttons with no key. */
const LABEL_KEYS = 9;

function LabelChoices({ labels, chosen, assignOnly, disabled, onChoose }: {
  labels: string[];
  chosen: string | null;
  assignOnly: boolean;
  disabled?: boolean;
  onChoose: (label: string) => void;
}) {
  return (
    <div role="group" aria-label="Label" className="flex flex-wrap gap-2" data-testid="label-choices">
      {labels.map((l, i) => {
        const key = assignOnly && i < LABEL_KEYS ? String(i + 1) : undefined;
        const on = l === chosen;
        return (
          <button key={l} type="button" data-key={key} aria-keyshortcuts={key} aria-pressed={on} disabled={disabled} onClick={() => onChoose(l)}
            className={`min-w-[6rem] flex-1 rounded-control border px-4 py-2.5 text-sm font-semibold disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500 ${on ? 'border-indigo-500 bg-indigo-500 text-white' : 'border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800'}`}>
            {key && <kbd aria-hidden="true" className={`mr-1.5 font-mono text-xs font-normal ${on ? 'text-white' : 'text-slate-500 dark:text-slate-400'}`}>{key}</kbd>}
            {l}
          </button>
        );
      })}
    </div>
  );
}

export function DecisionBar({ kind, labels, onDecide, hidden, pending, chosen = null }: {
  kind: QueueKind;
  labels: string[];
  onDecide: (body: DecisionIn) => void;
  hidden: boolean;
  pending?: boolean;
  /** The row's current answer, if it has one. */
  chosen?: string | null;
}) {
  const [reason, setReason] = useState('');
  const [label, setLabel] = useState(chosen);
  const needsReason = !reason.trim();
  const btn = 'rounded-control border px-3 py-1.5 text-sm disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500';
  const assignOnly = kind === 'calibration_labeling';
  return (
    <div className="stack-y-2" data-testid="decision-bar">
      <LabelChoices labels={labels} chosen={assignOnly ? chosen : label} assignOnly={assignOnly} disabled={pending}
        onChoose={assignOnly ? (l) => onDecide({ decision: 'override', override_label: l, reason: reason || undefined }) : setLabel} />
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="Reason" placeholder={assignOnly ? 'Reason (required to flag)' : 'Reason (required to override, flag or reject)'} value={reason} onChange={(e) => setReason(e.target.value)}
          className="min-w-[16rem] flex-1 rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm outline-hidden focus-visible:ring-2 focus-visible:ring-indigo-500" />
      </div>
      <div className="flex flex-wrap gap-2">
        {!(assignOnly && hidden) && (
          <button type="button" data-key="a" disabled={pending} className={`${btn} border-green-400 text-green-800 dark:text-green-300`} onClick={() => onDecide({ decision: 'accept', reason: reason || undefined })}>
            Accept label
          </button>
        )}
        {!assignOnly && (
          <button type="button" data-key="o" disabled={pending || needsReason || !label} className={`${btn} border-indigo-400`}
            onClick={() => label && onDecide({ decision: 'override', override_label: label, reason: reason || undefined })}>
            Override label
          </button>
        )}
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

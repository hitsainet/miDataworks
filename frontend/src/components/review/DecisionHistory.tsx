// An item's decisions, oldest first; never edited (decisions are append-only).
import type { Decision } from '@/types/review';

import { DecidedBy } from './DecidedBy';

export function DecisionHistory({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) return <p className="text-xs text-slate-500 dark:text-slate-400">No decisions yet.</p>;
  return (
    <ol className="space-y-1 text-xs" data-testid="decision-history">
      {decisions.map((d) => (
        <li key={d.id}>
          <DecidedBy who={d.decided_by} origin={d.decided_by_origin} /> · {d.decision}
          {d.override_label ? ` → ${d.override_label}` : ''} · {d.reason}
          {!d.model_output_visible && ' · model output hidden'} · {new Date(d.created_at).toLocaleString()}
        </li>
      ))}
    </ol>
  );
}

// One set in the list: name, role summary, last send state and last rung in miStudio's words.
import type { DetectorSetSummary } from '@/types/detectorSets';

export function DetectorSetCard({ set, onOpen }: { set: DetectorSetSummary; onOpen: () => void }) {
  const roles = Object.entries(set.role_counts).map(([r, n]) => `${n} ${r.replace('_', ' ')}`).join(', ');
  return (
    <button type="button" onClick={onOpen} className="w-full rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-4 text-left focus-visible:ring-2 focus-visible:ring-indigo-500" data-testid={`set-card-${set.name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{set.name}{set.archived ? ' (archived)' : ''}</span>
        {set.last_rung && <span className="rounded-full bg-emerald-100 dark:bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-800 dark:text-emerald-200">miStudio · {set.last_rung.rung_language}</span>}
      </div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{roles || 'No roles yet'} · {set.last_send_state ? `last send ${set.last_send_state}` : 'not sent'}</div>
    </button>
  );
}

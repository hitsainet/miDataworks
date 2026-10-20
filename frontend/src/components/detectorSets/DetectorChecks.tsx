// D-1 to D-8 as callouts with a TEXT pill (no colour-only signal), reason and next step.
import type { CheckOutcome, CheckOutcomeName } from '@/types/detectorSets';

const PILL: Record<CheckOutcomeName, { text: string; cls: string }> = {
  green: { text: 'Green', cls: 'bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-300' },
  note: { text: 'Note', cls: 'bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-200' },
  refused: { text: 'Refused', cls: 'bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300' },
};

export function DetectorChecks({ outcomes }: { outcomes: CheckOutcome[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="detector-checks">
      {outcomes.map((o) => (
        <div key={o.code} className="rounded-lg border border-slate-200 dark:border-slate-700/60 p-3 text-sm" data-testid={`check-${o.code}`}>
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-medium">{o.code} · {o.title}</span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${PILL[o.outcome].cls}`}>{PILL[o.outcome].text}</span>
          </div>
          {o.figure && <div className="text-xs tabular-nums text-slate-500 dark:text-slate-400">{o.figure}</div>}
          <p className="mt-1 text-slate-700 dark:text-slate-200">{o.reason}</p>
          {o.next_step && o.outcome !== 'green' && <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">Next: {o.next_step}</p>}
        </div>
      ))}
    </div>
  );
}

// Checks before pushing (FR-008.11; 008 FTASKS 11.2). Each outcome is shown with an icon AND text,
// never colour alone (FPRD 008 section 4.5).
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react';

import { Card } from '@/components/common/Card';
import type { CheckOutcome, CheckRun, OutcomeLevel } from '@/types/publishing';

const STYLE: Record<OutcomeLevel, { icon: typeof Info; word: string; cls: string }> = {
  green: { icon: CheckCircle2, word: 'Passes', cls: 'border-emerald-300 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-800 dark:text-emerald-200' },
  note: { icon: Info, word: 'Note', cls: 'border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-800/60 text-slate-700 dark:text-slate-200' },
  amber: { icon: AlertTriangle, word: 'Blocks a public push', cls: 'border-amber-300 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 text-amber-900 dark:text-amber-200' },
  refused: { icon: XCircle, word: 'Blocks every push', cls: 'border-red-300 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 text-red-800 dark:text-red-200' },
};

export function CheckCallout({ outcome }: { outcome: CheckOutcome }) {
  const style = STYLE[outcome.outcome];
  const Icon = style.icon;
  return (
    <div className={`flex gap-2 rounded-lg border px-3 py-2 text-sm ${style.cls}`} data-testid={`check-${outcome.check}`} data-outcome={outcome.outcome}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <div className="font-medium">
          {outcome.check} · {style.word}
        </div>
        <div>{outcome.reason}</div>
        {outcome.outcome !== 'green' && <div className="text-xs mt-0.5 opacity-80">{outcome.next_step}</div>}
      </div>
    </div>
  );
}

export function ChecksPanel({ run, pending }: { run: CheckRun | null; pending: boolean }) {
  const results = run?.results ?? [];
  return (
    <Card className="lg:col-span-2 min-w-0" data-testid="checks-panel">
      <div className="font-semibold mb-3">Checks before pushing</div>
      {pending || (run && run.status !== 'completed') ? (
        <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> Running checks in the publish worker (the token check needs it).
        </div>
      ) : !run ? (
        <div className="text-sm text-slate-500 dark:text-slate-400">Preview the files and name a repository; the checks run on their own.</div>
      ) : (
        <div className="space-y-2">
          {results.map((r) => (
            <CheckCallout key={r.check} outcome={r} />
          ))}
        </div>
      )}
      <div className="text-xs mt-3 text-slate-500 dark:text-slate-400">
        After the push, the Hub&apos;s file hashes (SHA-256, the Secure Hash Algorithm digest of each file) are compared with the files built. A mismatch fails the publish and the repository stays private.
      </div>
    </Card>
  );
}

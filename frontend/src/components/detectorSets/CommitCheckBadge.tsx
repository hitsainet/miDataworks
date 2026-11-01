// Feature 011 (FR-011.8): whether miStudio confirmed the commit a role was downloaded at. The phrase
// is the server's (services/detector_sets/commit_check.py), rendered as text, never as HTML.
import type { CommitCheck } from '@/types/detectorSets';

const TONE: Record<string, string> = {
  verified: 'bg-emerald-100 dark:bg-emerald-500/15 text-emerald-800 dark:text-emerald-200',
  claimed: 'bg-amber-100 dark:bg-amber-500/15 text-amber-800 dark:text-amber-200',
  none: 'bg-slate-100 dark:bg-slate-700/40 text-slate-700 dark:text-slate-300',
};

export function CommitCheckBadge({ check, phrase }: { check: CommitCheck | null; phrase: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ${TONE[check ?? 'none']}`} data-testid="commit-check" data-check={check ?? 'none'}>
      {phrase}
    </span>
  );
}

// The licence as imported (FR-001.26): the Hub's value, or "not stated" with what to do next.
// Text plus colour, never colour alone (PPRD section 7.2).
import { AlertTriangle } from 'lucide-react';

import { STATUS_CLASSES } from '@/config/brand';

export const NOT_STATED = 'not stated';
export const NOT_STATED_ADVICE = 'Licence not stated. You can publish this privately; record its terms before publishing publicly.';

export function LicenceBadge({ display, terms, verbose = false }: { display: string; terms?: string | null; verbose?: boolean }) {
  const missing = display === NOT_STATED;
  return (
    <span className="inline-flex flex-col gap-1">
      <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${missing ? STATUS_CLASSES.warn : STATUS_CLASSES.neutral}`} title={missing ? NOT_STATED_ADVICE : `Licence: ${display}`}>
        {missing && <AlertTriangle className="w-3 h-3" aria-hidden="true" />}
        Licence: {display}
      </span>
      {terms && terms !== 'not recorded' && <span className="text-xs text-slate-500 dark:text-slate-400">Terms recorded: {terms.replace(/_/g, ' ')}</span>}
      {missing && verbose && <span className="text-xs text-amber-700 dark:text-amber-400">{NOT_STATED_ADVICE}</span>}
    </span>
  );
}

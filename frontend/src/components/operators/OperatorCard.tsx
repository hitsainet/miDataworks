// One operator in the catalogue (FR-003.22; mockup Operators()). Native is the indigo accent; other
// providers are dim. A state other than allowed says why and what to do next.
import { Blocks, Filter } from 'lucide-react';

import { Badge } from '@/components/common/Badge';
import type { OperatorEntry, OperatorState } from '@/types/operators';

export const STATE_TEXT: Record<OperatorState, string> = {
  allowed: 'Allowed',
  not_allowed: 'Not allowed',
  invalid_manifest: 'Invalid manifest',
  failed_to_load: 'Failed to load',
  duplicate: 'Duplicate',
};

const STATE_VARIANT: Record<OperatorState, 'success' | 'warning' | 'danger' | 'neutral'> = {
  allowed: 'success',
  not_allowed: 'warning',
  invalid_manifest: 'danger',
  failed_to_load: 'danger',
  duplicate: 'danger',
};

export function providerLabel(provider: string): string {
  if (provider === 'native') return 'Native';
  if (provider === 'datajuicer') return 'Data-Juicer';
  if (provider === 'data_designer') return 'Data Designer';
  return provider.replace(/^plugin:/, 'Plugin: ');
}

export function nextStep(entry: OperatorEntry): string | null {
  if (entry.state === 'not_allowed') return 'Allow its entry point below to preview or run it.';
  if (entry.state === 'allowed') return null;
  return entry.error ?? 'It cannot run; see its manifest for the reason.';
}

export function OperatorCard({ entry, onOpen }: { entry: OperatorEntry; onOpen: (entry: OperatorEntry) => void }) {
  const native = entry.provider === 'native';
  const Icon = native ? Blocks : Filter;
  const next = nextStep(entry);
  return (
    <button
      type="button"
      data-testid="operator-card"
      onClick={() => onOpen(entry)}
      className="text-left w-full rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-4 hover:border-indigo-400/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
    >
      <div className="flex items-start gap-3">
        <span className={`rounded-lg p-2 ${native ? 'bg-indigo-500/15 text-indigo-500' : 'bg-slate-500/10 text-slate-500 dark:text-slate-400'}`}>
          <Icon size={18} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-sm text-slate-900 dark:text-slate-100 truncate">
            {entry.name} <span className="text-slate-500 dark:text-slate-400">{entry.version}</span>
          </div>
          <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">{entry.description ?? 'Manifest not loaded.'}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {entry.kind ? <Badge variant="neutral" size="sm">{entry.kind}</Badge> : null}
            <Badge variant={native ? 'primary' : 'neutral'} size="sm">{providerLabel(entry.provider)}</Badge>
            <Badge variant={STATE_VARIANT[entry.state]} size="sm">{STATE_TEXT[entry.state]}</Badge>
            {entry.has_thresholds ? <Badge variant="neutral" size="sm">threshold</Badge> : null}
          </div>
          {next ? <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{next}</p> : null}
        </div>
      </div>
    </button>
  );
}

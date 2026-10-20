// Every audited column: figure, chance, control and a status pill WITH TEXT (ADR-020), then the
// excluded columns with their reasons (FR-004.28).
import { Badge } from '@/components/common/Badge';
import type { EvaluatedWarning, ShortcutAudit, ShortcutColumn } from '@/types/curation';

import { count, pct, pretty } from './format';

export function columnStatus(column: ShortcutColumn, warnings: EvaluatedWarning[]): { text: string; variant: 'warning' | 'success' | 'danger' } {
  if (!column.valid) return { text: 'Control failed', variant: 'danger' };
  if (warnings.some((w) => w.column === column.column)) return { text: 'Warns', variant: 'warning' };
  return { text: 'Clear', variant: 'success' };
}

const REASONS: Record<string, string> = {
  content: 'content (the signal, not a shortcut)',
  system: 'system column',
  label: 'the label itself',
  label_derived: 'derived from the label',
};

export function ShortcutColumnTable({ audit, warnings }: { audit: ShortcutAudit; warnings: EvaluatedWarning[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs min-w-[36rem]">
        <caption className="sr-only">Shortcut audit, one row per audited column</caption>
        <thead>
          <tr className="text-left text-slate-500 dark:text-slate-400">
            <th className="py-1 pr-2">Column</th>
            <th className="py-1 pr-2">Predicts label (held-out balanced accuracy)</th>
            <th className="py-1 pr-2">Chance</th>
            <th className="py-1 pr-2">Permuted-label control</th>
            <th className="py-1 pr-2">Values</th>
            <th className="py-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {audit.columns.map((c) => {
            const status = columnStatus(c, warnings);
            return (
              <tr key={c.column} className="border-t border-slate-200 dark:border-slate-700">
                <td className="py-1 pr-2 font-mono">{c.column}</td>
                <td className="py-1 pr-2">{pct(c.figure)} on {count(c.n_rows)} rows</td>
                <td className="py-1 pr-2">{pct(c.chance, 0)}</td>
                <td className="py-1 pr-2" title={c.invalid_reason ?? undefined}>{pct(c.control_mean)}</td>
                <td className="py-1 pr-2">{count(c.n_values)}</td>
                <td className="py-1"><Badge variant={status.variant}>{status.text}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {audit.excluded_columns.length > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">
          Not scored:{' '}
          {audit.excluded_columns.map((e, i) => (
            <span key={e.column}>
              {i > 0 && ', '}
              <span className="font-mono">{e.column}</span> ({REASONS[e.reason] ?? e.reason}
              {e.source_operator ? `, written by ${e.source_operator}` : ''})
            </span>
          ))}
          . {pretty(audit.label_column)} has {audit.classes.length} classes, so chance is {pct(audit.chance, 0)}.
        </p>
      )}
    </div>
  );
}

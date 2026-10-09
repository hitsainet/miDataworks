// Value x label counts for one audited column, with the labeler's excluded rows in a dimmed column
// (FR-004.32; the source-against-label cross-tab, P-23/X-04). Clicking a count opens its samples.
import type { ShortcutAudit } from '@/types/curation';

import { count } from './format';

interface Props {
  audit: ShortcutAudit;
  column: string;
  onCell: (value: string, label: string) => void;
}

export function CrossTabTable({ audit, column, onCell }: Props) {
  const result = audit.columns.find((c) => c.column === column);
  if (!result) return null;
  const excluded = audit.excluded_by_band[column] ?? null;
  return (
    <div className="overflow-x-auto">
      <table className="text-xs min-w-[24rem]">
        <caption className="sr-only">{column} against {audit.label_column}</caption>
        <thead>
          <tr className="text-left text-slate-500 dark:text-slate-400">
            <th className="pr-3">{column}</th>
            {audit.classes.map((c) => (
              <th key={c} className="pr-3">{c}</th>
            ))}
            {excluded && <th className="pr-3 opacity-60">Excluded by the labeler</th>}
          </tr>
        </thead>
        <tbody>
          {result.per_value.values.map((row) => (
            <tr key={row.value} className="border-t border-slate-200 dark:border-slate-700">
              <td className="pr-3 font-mono">{row.value}</td>
              {audit.classes.map((c) => (
                <td key={c} className="pr-3">
                  <button
                    type="button"
                    className="underline decoration-dotted focus-visible:ring-2 focus-visible:ring-indigo-500"
                    onClick={() => onCell(row.value, c)}
                    aria-label={`Samples for ${row.value} and ${c}`}
                  >
                    {count(row.counts_by_label[c] ?? 0)}
                  </button>
                </td>
              ))}
              {excluded && <td className="pr-3 opacity-60">{count(excluded[row.value] ?? 0)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {result.per_value.other_values > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">{result.per_value.other_values} smaller values are grouped as other.</p>
      )}
    </div>
  );
}

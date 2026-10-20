// "Why did this row leave?" (FR-002.26): present (with its changes), dropped (with the step,
// operator, reason and statistic), or never seen (naming what was searched).
import { X } from 'lucide-react';

import type { RowHistory } from '@/types/versions';

import { IdentifierChip } from './IdentifierChip';

function statistic(entry: RowHistory['trail'][number]): string {
  if (entry.statistic_value !== null) return `${entry.statistic_name} = ${entry.statistic_value}`;
  if (entry.statistic_text) return `${entry.statistic_name} = ${entry.statistic_text}`;
  return '';
}

export function RowHistoryResult({ result }: { result: RowHistory }) {
  return (
    <div className="border-t border-slate-200 dark:border-slate-700 pt-3 mt-3 text-xs" data-testid="row-history-result">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <IdentifierChip value={result.row_key} label="row key" />
        <span className="font-semibold" data-testid="row-status">
          {result.status === 'present' ? 'Present in this version' : result.status === 'dropped' ? 'Dropped' : 'Not found'}
        </span>
      </div>
      {result.dropped_at && (
        <p className="mb-2">
          Left at version {result.dropped_at.version_number}, step {result.dropped_at.step_index} ({result.dropped_at.operator} {result.dropped_at.operator_version}):{' '}
          <span className="font-medium">{result.dropped_at.reason}</span> {statistic(result.dropped_at) && <span className="font-mono">[{statistic(result.dropped_at)}]</span>}
        </p>
      )}
      {result.present_in.length > 0 && (
        <p className="mb-2">In split {result.present_in.map((p) => `${p.split} (copy ${p.occurrence})`).join(', ')}.</p>
      )}
      {result.trail.length > 0 && (
        <ol className="space-y-1 mb-2">
          {result.trail.map((t, i) => (
            <li key={i} className="font-mono">
              v{t.version_number} step {t.step_index} · {t.kind} · {t.reason_code}
              {t.to_key && t.to_key !== t.from_key ? ` · became ${t.to_key.slice(0, 12)}…` : ''}
            </li>
          ))}
        </ol>
      )}
      {result.origin && (
        <p className="text-slate-500 dark:text-slate-400">
          Origin: {result.origin.generated ? 'generated from another row' : `source ${result.origin.source_id?.slice(0, 8)}… at ${result.origin.source_locator}`}
        </p>
      )}
      {result.status === 'not_found' && (
        <p className="text-slate-500 dark:text-slate-400">Searched {result.searched.length} version(s) in this lineage. Check the key, or search by text.</p>
      )}
    </div>
  );
}

export function RowHistoryDrawer({ results, onClose }: { results: RowHistory[]; onClose: () => void }) {
  return (
    <aside className="fixed inset-y-0 right-0 w-full max-w-lg bg-white dark:bg-slate-900 border-l border-slate-200 dark:border-slate-700 shadow-xl p-5 overflow-y-auto z-40" role="dialog" aria-label="Why did this row leave?">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-semibold">Why did this row leave?</h2>
        <button type="button" aria-label="Close row history" onClick={onClose} className="p-1 rounded focus-visible:ring-2 focus-visible:ring-indigo-500">
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
      {results.length === 0 ? <p className="text-sm text-slate-500">No row matched.</p> : results.map((r) => <RowHistoryResult key={r.row_key} result={r} />)}
    </aside>
  );
}

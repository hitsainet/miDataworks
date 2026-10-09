// The row browser (FR-002.33, FR-002.26): pages of rows read from Parquet by DuckDB, a text search,
// and "Why did this row leave?" for a key or text that is no longer in the version.
import { Search } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useVersionsStore } from '@/stores/versionsStore';
import type { Version } from '@/types/versions';

import { IdentifierChip } from './IdentifierChip';
import { RowHistoryDrawer } from './RowHistoryDrawer';

export function RowBrowser({ version }: { version: Version }) {
  const rows = useVersionsStore((s) => s.rows);
  const history = useVersionsStore((s) => s.history);
  const fetchRows = useVersionsStore((s) => s.fetchRows);
  const findRowHistory = useVersionsStore((s) => s.findRowHistory);
  const clearHistory = useVersionsStore((s) => s.clearHistory);
  const [split, setSplit] = useState<string>('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [lookup, setLookup] = useState('');
  const deleted = version.state === 'deleted';
  const content = Object.entries(version.column_roles).filter(([, r]) => r === 'content').map(([c]) => c);

  useEffect(() => {
    if (!deleted) void fetchRows(version.id, { split: split || undefined, q: query || undefined, page });
  }, [version.id, split, query, page, deleted, fetchRows]);

  const ask = () => {
    const value = lookup.trim();
    if (!value) return;
    void findRowHistory(version.id, /^[0-9a-f]{12,64}$/i.test(value) ? { row_key: value.toLowerCase() } : { q: value });
  };

  return (
    <section className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 min-w-0" aria-labelledby="rows-title">
      <h2 id="rows-title" className="font-semibold mb-3">Rows</h2>
      <div className="flex flex-wrap gap-2 mb-3">
        <label className="sr-only" htmlFor="row-lookup">Row key or text</label>
        <input
          id="row-lookup"
          value={lookup}
          onChange={(e) => setLookup(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
          placeholder="Row key (12+ characters) or text"
          className="flex-1 min-w-[12rem] rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        />
        <Button variant="secondary" leftIcon={<Search className="w-4 h-4" />} onClick={ask}>Why did this row leave?</Button>
      </div>
      {deleted ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">This version was deleted; its rows are gone. Row history still answers from its events.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-3 text-sm">
            <label className="flex items-center gap-2">
              <span className="text-slate-500 dark:text-slate-400">Split</span>
              <select value={split} onChange={(e) => { setSplit(e.target.value); setPage(1); }} className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 focus-visible:ring-2 focus-visible:ring-indigo-500">
                <option value="">All splits</option>
                {version.splits.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 flex-1">
              <span className="text-slate-500 dark:text-slate-400">Contains</span>
              <input value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }} className="flex-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 focus-visible:ring-2 focus-visible:ring-indigo-500" />
            </label>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 dark:text-slate-400">
                  <th className="pb-2 font-medium">Row key</th>
                  {content.map((c) => <th key={c} className="pb-2 font-medium">{c}</th>)}
                  <th className="pb-2 font-medium">Split</th>
                  <th className="pb-2 font-medium"><span className="sr-only">History</span></th>
                </tr>
              </thead>
              <tbody>
                {(rows?.items ?? []).map((row, i) => (
                  <tr key={`${String(row._dw_row_key)}-${String(row._dw_occurrence)}-${i}`} className="border-t border-slate-200 dark:border-slate-700 align-top">
                    <td className="py-1.5 pr-2"><IdentifierChip value={String(row._dw_row_key)} /></td>
                    {content.map((c) => <td key={c} className="py-1.5 pr-2 max-w-md break-words">{typeof row[c] === 'string' ? (row[c] as string) : JSON.stringify(row[c])}</td>)}
                    <td className="py-1.5 pr-2">{String(row._dw_split)}</td>
                    <td className="py-1.5">
                      <button type="button" className="text-indigo-700 dark:text-indigo-300 underline rounded focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => void findRowHistory(version.id, { row_key: String(row._dw_row_key) })}>
                        History
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between mt-3 text-xs text-slate-500 dark:text-slate-400">
            <span>{rows ? `${rows.total.toLocaleString('en-US')} rows match` : 'Loading rows…'}</span>
            <span className="flex gap-2">
              <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous page</Button>
              <Button size="sm" variant="ghost" disabled={!rows || page * 50 >= rows.total} onClick={() => setPage(page + 1)}>Next page</Button>
            </span>
          </div>
        </>
      )}
      {history && <RowHistoryDrawer results={history} onClose={clearHistory} />}
    </section>
  );
}

// The sources list (FR-001.33): name, kind, state as text and colour, pinned commit or content hash,
// licence, splits and rows, detected goal, and import progress while running. It sits between the
// form and the card grid, collapsed when every source is ready (T-01).
import { ChevronRight, Database, FileUp } from 'lucide-react';

import { STATUS_CLASSES } from '@/config/brand';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { ImportProgress, SourceSummary } from '@/types/sources';
import { formatCount } from '@/utils/format';

import { CommitChip } from './CommitChip';
import { LicenceBadge } from './LicenceBadge';

const STATE_CLASS: Record<string, string> = {
  ready: STATUS_CLASSES.ok,
  importing: STATUS_CLASSES.running,
  queued: STATUS_CLASSES.neutral,
  running: STATUS_CLASSES.running,
  failed: STATUS_CLASSES.error,
  cancelled: STATUS_CLASSES.neutral,
  deleted: STATUS_CLASSES.neutral,
  completed: STATUS_CLASSES.ok,
};

export function StatePill({ state }: { state: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_CLASS[state] ?? STATUS_CLASSES.neutral}`}>{state.charAt(0).toUpperCase() + state.slice(1)}</span>;
}

function ImportRow({ item }: { item: ImportProgress }) {
  const done = item.status === 'completed';
  return (
    <li className="py-2 text-sm" data-testid="import-progress">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono truncate">{item.label}</span>
        <StatePill state={done && item.existing ? 'ready' : item.status} />
      </div>
      {!['completed', 'failed', 'cancelled'].includes(item.status) && (
        <>
          <div className="mt-1 h-1.5 rounded bg-slate-200 dark:bg-slate-700" role="progressbar" aria-valuenow={Math.round(item.progress)} aria-valuemin={0} aria-valuemax={100} aria-label={`Import of ${item.label}`}>
            <div className="h-1.5 rounded bg-indigo-500" style={{ width: `${Math.max(2, item.progress)}%` }} />
          </div>
          <p className="text-xs mt-1 text-slate-500 dark:text-slate-400">{item.phase ?? 'Queued: waiting for the import worker.'}</p>
        </>
      )}
      {done && item.existing && <p className="text-xs mt-1 text-slate-500 dark:text-slate-400">Already imported: nothing was downloaded, the existing source is used.</p>}
      {item.status === 'failed' && <p className="text-xs mt-1 text-red-700 dark:text-red-400">{item.error ?? 'The import failed; open Operations for the reason.'}</p>}
    </li>
  );
}

export function SourcesList({ onOpen }: { onOpen: (source: SourceSummary) => void }) {
  const sources = useSourcesStore((s) => s.sources);
  const imports = useSourcesStore((s) => s.imports);
  const loading = useSourcesStore((s) => s.loading);
  const tracked = Object.values(imports).filter((i) => i.status !== 'completed' || i.existing);
  const allReady = sources.every((s) => s.state === 'ready') && tracked.every((i) => ['completed', 'cancelled'].includes(i.status));

  return (
    <details open={!allReady || sources.length === 0} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 mb-6" data-testid="sources-list">
      <summary className="cursor-pointer font-semibold text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded">
        Sources ({formatCount(sources.length, 'source')})
      </summary>
      {tracked.length > 0 && <ul className="mt-3 divide-y divide-slate-200 dark:divide-slate-700">{tracked.map((i) => <ImportRow key={i.jobId} item={i} />)}</ul>}
      {loading && sources.length === 0 && <p role="status" className="mt-3 text-sm text-slate-500 dark:text-slate-400">Loading sources…</p>}
      {!loading && sources.length === 0 && (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400" data-testid="sources-empty">No sources yet. Import a Hugging Face dataset or upload a file above; each becomes a source you can build versions from.</p>
      )}
      <ul className="mt-3 divide-y divide-slate-200 dark:divide-slate-700">
        {sources.map((s) => (
          <li key={s.id} className="flex items-start justify-between gap-3 py-3">
            <span className="flex gap-3 min-w-0">
              {s.kind === 'hf' ? <Database className="w-4 h-4 mt-0.5 text-slate-400 shrink-0" aria-hidden="true" /> : <FileUp className="w-4 h-4 mt-0.5 text-slate-400 shrink-0" aria-hidden="true" />}
              <span className="min-w-0">
                <button type="button" onClick={() => onOpen(s)} className="block font-medium text-left text-slate-900 dark:text-slate-100 truncate max-w-full hover:underline rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" aria-label={`Open source ${s.display_name}`}>
                  {s.display_name}
                </button>
                <span className="flex flex-wrap items-center gap-2 text-xs mt-1 text-slate-500 dark:text-slate-400">
                  {s.kind === 'hf' ? 'Hugging Face' : 'Upload'}
                  {s.resolved_commit ? <CommitChip value={s.resolved_commit} label="commit" /> : <CommitChip value={s.content_hash} label="hash" />}
                  <span>{formatCount(s.rows, 'row')} in {formatCount(s.splits.length, 'split')}</span>
                  {s.suggested_target && <span>detected: {s.suggested_target}</span>}
                </span>
                <span className="block mt-1"><LicenceBadge display={s.licence_display} /></span>
              </span>
            </span>
            <span className="flex items-center gap-2 shrink-0">
              <StatePill state={s.state} />
              <ChevronRight className="w-4 h-4 text-slate-400" aria-hidden="true" />
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

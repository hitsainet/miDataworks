// Try before committing (FR-003.19): kept, changed and dropped rows from a sample, each drop with
// its reason and statistic in mono type. A preview writes nothing.
import { useState } from 'react';

import type { PreviewResult } from '@/types/operators';

type Tab = 'kept' | 'changed' | 'dropped';

export function PreviewPanel({ result }: { result: PreviewResult }) {
  const [tab, setTab] = useState<Tab>('dropped');
  if (result.empty) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">{result.message ?? 'No rows to preview.'}</p>;
  }
  const examples = result.examples;
  const counts = result.counts;
  return (
    <div className="space-y-3" data-testid="preview-panel">
      <p className="text-sm text-slate-700 dark:text-slate-300">
        Sample of {result.sample_size} rows (seed {result.seed}): kept {counts.kept}, changed {counts.changed}, dropped {counts.dropped}
        {counts.added ? `, added ${counts.added}` : ''}.
      </p>
      {result.note === 'within_sample_only' ? (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          This operator looks across rows, so the preview only compares rows within the sample; the full run may drop more.
        </p>
      ) : null}
      <div role="tablist" className="flex gap-2">
        {(['kept', 'changed', 'dropped'] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-400 ${tab === t ? 'bg-indigo-500 text-white' : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'}`}
          >
            {t[0].toUpperCase() + t.slice(1)} ({t === 'kept' ? counts.kept : t === 'changed' ? counts.changed : counts.dropped})
          </button>
        ))}
      </div>
      <ul className="space-y-2" data-testid={`preview-${tab}`}>
        {tab === 'kept' && examples?.kept.map((k) => <li key={`${k.row_key}:${k.occurrence}`} className="text-sm text-slate-700 dark:text-slate-300">{k.excerpt}</li>)}
        {tab === 'changed' &&
          examples?.changed.map((c) => (
            <li key={`${c.row_key}:${c.occurrence}`} className="text-sm text-slate-700 dark:text-slate-300">
              <span className="line-through opacity-70">{c.before}</span> → {c.after}
              <div className="font-mono text-xs text-slate-500 dark:text-slate-400">{c.reason_code}: {c.reason}</div>
            </li>
          ))}
        {tab === 'dropped' &&
          examples?.dropped.map((d) => (
            <li key={`${d.row_key}:${d.occurrence}`} className="text-sm text-slate-700 dark:text-slate-300">
              {d.excerpt || '(empty text)'}
              <div className="font-mono text-xs text-slate-500 dark:text-slate-400">
                {d.reason_code}: {d.reason}
                {d.statistic_name ? ` · ${d.statistic_name}=${d.statistic_value ?? d.statistic_text}` : ''}
                {d.threshold ? ` · threshold ${d.threshold}` : ''}
              </div>
            </li>
          ))}
      </ul>
    </div>
  );
}

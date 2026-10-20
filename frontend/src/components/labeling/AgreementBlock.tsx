// A run against the label column its data already has (novice re-walk 2026-10-19, finding 6: a
// probe labeled 11.9% of ColBERT positive while its own humor column is about half positive, and
// nothing said so). Lists the version's two-valued columns as a dropdown, asks which value means
// positive when the column does not say, and shows the four counts, the agreement, each side's
// positive share and the AUROC, with the server's plain-language note.
import { useEffect, useState } from 'react';

import { ApiError } from '@/api/client';
import { labelingApi } from '@/api/labeling';
import type { LabelAgreement } from '@/types/labeling';

const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`);

export function AgreementBlock({ runId, state }: { runId: string; state: string }) {
  const [column, setColumn] = useState<string | null>(null);
  const [positive, setPositive] = useState<string | null>(null);
  const [answer, setAnswer] = useState<LabelAgreement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    labelingApi
      .agreement(runId, column, positive)
      .then((a) => {
        if (!live) return;
        setAnswer(a);
        setError(null);
      })
      .catch((e: unknown) => live && setError(e instanceof ApiError ? e.message : 'The comparison could not be read.'));
    return () => {
      live = false;
    };
  }, [runId, state, column, positive]);

  if (error) return <p className="text-sm text-red-600 dark:text-red-400 mb-3" role="alert" data-testid="agreement-error">{error}</p>;
  if (!answer) return null;
  const field = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  const c = answer.counts;
  return (
    <section className="mb-3 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="agreement" aria-labelledby={`agreement-${runId}`}>
      <h3 id={`agreement-${runId}`} className="text-sm font-semibold mb-1">Against the label column in the data</h3>
      {answer.columns.length > 0 && (
        <div className="flex flex-wrap gap-3 mb-2 text-xs">
          <label>
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Column</span>
            <select aria-label="Label column to compare with" className={field} value={answer.column ?? ''} onChange={(e) => { setColumn(e.target.value); setPositive(null); }}>
              {answer.columns.map((col) => <option key={col.name} value={col.name}>{col.name} ({col.values.join(' or ')})</option>)}
            </select>
          </label>
          {answer.values.length === 2 && (answer.needs_positive_value || positive !== null) && (
            <label>
              <span className="block text-slate-500 dark:text-slate-400 mb-1">Which value means positive?</span>
              <select aria-label="Value that means positive" className={field} value={positive ?? ''} onChange={(e) => setPositive(e.target.value || null)}>
                <option value="">Choose a value</option>
                {answer.values.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      {!answer.applicable ? (
        <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="agreement-reason">{answer.reason}</p>
      ) : (
        <>
          <p className="text-sm mb-2" data-testid="agreement-note">{answer.note}</p>
          {c && (
            <table className="text-xs mb-1 tabular" data-testid="agreement-counts">
              <thead>
                <tr><th scope="col" /><th scope="col" className="px-2 text-left font-medium">Column: {answer.positive_value}</th><th scope="col" className="px-2 text-left font-medium">Column: other</th></tr>
              </thead>
              <tbody>
                <tr><th scope="row" className="pr-2 text-left font-medium">Run: positive</th><td className="px-2">{c.both_positive.toLocaleString()}</td><td className="px-2">{c.run_only_positive.toLocaleString()}</td></tr>
                <tr><th scope="row" className="pr-2 text-left font-medium">Run: negative</th><td className="px-2">{c.column_only_positive.toLocaleString()}</td><td className="px-2">{c.both_negative.toLocaleString()}</td></tr>
              </tbody>
            </table>
          )}
          <p className="text-xs text-slate-500 dark:text-slate-400" data-testid="agreement-figures">
            {answer.rows_compared.toLocaleString()} rows compared · agree {pct(answer.accuracy)} · run positive {pct(answer.run_positive_share)} · column positive {pct(answer.column_positive_share)}
            {answer.auroc !== null ? ` · AUROC ${answer.auroc.toFixed(3)} from the ${answer.auroc_from}` : ''}
            {answer.rows_not_compared > 0 ? ` · ${answer.rows_not_compared.toLocaleString()} rows not compared (skipped, excluded or without a value)` : ''}
          </p>
        </>
      )}
    </section>
  );
}

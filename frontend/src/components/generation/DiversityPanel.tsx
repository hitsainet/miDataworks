// The diversity report (FR-007.38 – 007.43): each figure with its interval beside the reference's,
// the sample, the embedding model or "lexical", the two controls and the verdict. A falling verdict
// warns and says what to do; it refuses nothing (P-01).
import { useEffect } from 'react';

import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { generationApi } from '@/api/generation';
import { useGenerationStore } from '@/stores/generationStore';

import { FIGURE_LABEL, fmt } from './format';

export const FALLING_ADVICE = 'Cap the largest cluster or generate from more seed rows.';

export function DiversityPanel({ versionId }: { versionId: string }) {
  const report = useGenerationStore((s) => s.diversity[versionId]);
  const loadDiversity = useGenerationStore((s) => s.loadDiversity);
  const requestDiversity = useGenerationStore((s) => s.requestDiversity);
  useEffect(() => {
    void loadDiversity(versionId);
  }, [versionId, loadDiversity]);
  if (report === undefined) return <p className="text-sm text-slate-500 dark:text-slate-400">Loading the diversity report…</p>;
  if (report === null) {
    return (
      <div className="space-y-2" data-testid="diversity-panel">
        <p className="text-sm text-slate-500 dark:text-slate-400">No diversity report yet for this version.</p>
        <Button size="sm" onClick={() => void requestDiversity(versionId)}>Measure diversity</Button>
      </div>
    );
  }
  const verdictVariant = report.verdict === 'holds' ? 'success' : report.verdict === 'falls' ? 'warning' : report.verdict === 'invalid' ? 'danger' : 'default';
  return (
    <div className="space-y-3" data-testid="diversity-panel">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={verdictVariant}>{report.verdict === 'not_measured' ? 'Not measured' : report.verdict}</Badge>
        <span className="text-slate-600 dark:text-slate-300">
          {report.column} on up to {report.sample_size.toLocaleString()} rows per side ({report.splits.join(', ')}) · embeddings {report.embedding_identity?.served_model ?? 'not measured (lexical only)'} · clusters {report.clustering.basis ?? 'lexical'}
        </span>
      </div>
      {report.verdict === 'falls' && (
        <div role="alert" className="rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm">
          The generated rows narrowed the data. {FALLING_ADVICE} Publishing is not refused; the card says so.
        </div>
      )}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500 dark:text-slate-400"><th>Figure</th><th>Version (95% interval)</th><th>Reference (95% interval)</th><th>Verdict</th></tr>
        </thead>
        <tbody>
          {Object.entries(report.figures).map(([key, f]) => (
            <tr key={key} className="border-t border-slate-200 dark:border-slate-700">
              <td className="py-1">{FIGURE_LABEL[key] ?? key}</td>
              <td>{f.version ? `${fmt(f.version.value)} [${fmt(f.version.lo)}, ${fmt(f.version.hi)}]` : 'not measured'}</td>
              <td>{f.reference ? `${fmt(f.reference.value)} [${fmt(f.reference.lo)}, ${fmt(f.reference.hi)}]` : 'not measured'}</td>
              <td>{f.verdict === 'not_measured' ? 'not measured' : f.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="text-xs text-slate-600 dark:text-slate-300">
        {report.checks.map((c) => (
          <li key={c.check_id}>{c.check_id === 'diversity_negative_control' ? 'Collapsed-sample control' : 'Split-half control'}: {c.result}{c.reason ? ` — ${c.reason}` : ''}</li>
        ))}
      </ul>
      <Button variant="secondary" size="sm" onClick={() => void generationApi.audit(versionId)}>Draw an audit by origin and side</Button>
    </div>
  );
}

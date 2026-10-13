// A run's detail (FPRD 005 section 4.2): counts by outcome, the histogram with both thresholds,
// the keep share (estimate and actual), latency percentiles, skipped rows with reasons, parse
// failures with raw outputs, the length correlation, provenance, and the lifecycle buttons.
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { useLabelRun } from '@/hooks/useLabelRun';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import type { LabelRun } from '@/types/labeling';

import { STATE_LABEL, coverageText, rate } from './format';
import { KeepShareLine } from './KeepShareLine';
import { ProbabilityHistogram } from './ProbabilityHistogram';
import { ProbeRunBlock } from './ProbeLines';
import { ProvenanceBlock } from './ProvenanceBlock';

function percentile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

export function LabelRunDetail({ run }: { run: LabelRun }) {
  const labels = useLabelRunsStore((s) => s.labels[run.id]);
  const live = useLabelRunsStore((s) => s.live[run.id]);
  const fetchLabels = useLabelRunsStore((s) => s.fetchLabels);
  const cancelRun = useLabelRunsStore((s) => s.cancelRun);
  const resumeRun = useLabelRunsStore((s) => s.resumeRun);
  const rederiveRun = useLabelRunsStore((s) => s.rederiveRun);
  const [pos, setPos] = useState('');
  const [neg, setNeg] = useState('');
  useLabelRun(run.id);

  useEffect(() => {
    void fetchLabels(run.id);
  }, [fetchLabels, run.id, run.state]);

  const probabilities = useMemo(() => (labels ?? []).flatMap((l) => (l.probability === null ? [] : [l.probability])), [labels]);
  const latencies = useMemo(() => (labels ?? []).flatMap((l) => (l.latency_ms === null ? [] : [l.latency_ms])), [labels]);
  const skipped = (labels ?? []).filter((l) => l.outcome === 'skipped');
  const parseFailures = (labels ?? []).filter((l) => l.outcome === 'parse_failure');
  const counts = live?.counts ?? run.counts;
  const live_ = run.state === 'queued' || run.state === 'running';
  // The server says whether Resume applies; a run the reproduction gate stopped is not resumable
  // (finding 3: a resume would fail the same way). Older payloads without the field fall back.
  const resumable = run.resumable ?? (run.state === 'cancelled' || run.state === 'failed');
  const positive = Number(pos);
  const negative = Number(neg);
  const rederiveValid = pos !== '' && neg !== '' && negative >= 0 && positive <= 1 && negative < positive;
  const field = 'w-24 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm font-mono focus-visible:ring-2 focus-visible:ring-indigo-500';

  return (
    <Card data-testid="label-run-detail" className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="font-semibold">Run {run.id.slice(0, 12)} · {STATE_LABEL[run.state] ?? run.state}</h2>
        <div className="flex gap-2">
          {live_ && <Button variant="danger" size="sm" onClick={() => void cancelRun(run.id)}>Cancel run</Button>}
          {resumable && <Button size="sm" onClick={() => void resumeRun(run.id)}>Resume run</Button>}
        </div>
      </div>
      {run.error && <div role="alert" className="mb-3 rounded border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm">{run.error.message}</div>}
      {run.not_resumable_reason && <p className="mb-3 text-sm text-slate-700 dark:text-slate-300" data-testid="not-resumable">{run.not_resumable_reason}</p>}
      {run.row_coverage && run.row_coverage.rows !== run.row_coverage.row_keys && (
        <p className="mb-3 text-sm text-slate-600 dark:text-slate-300" data-testid="run-coverage">{coverageText(run.rows_total, run.row_coverage)}.</p>
      )}
      {run.kind === 'probe_verdict' && <ProbeRunBlock run={run} />}
      <div className="flex flex-wrap gap-3 mb-3 text-sm" data-testid="outcome-counts">
        {Object.entries(counts).map(([k, v]) => <span key={k} className="rounded bg-slate-100 dark:bg-slate-900 px-2 py-1">{k} {v.toLocaleString()}</span>)}
        {run.rows_reused > 0 && <span className="rounded bg-slate-100 dark:bg-slate-900 px-2 py-1">reused {run.rows_reused.toLocaleString()}</span>}
        {live && rate(live.rows_per_second) && <span className="text-slate-500 dark:text-slate-400">{rate(live.rows_per_second)}</span>}
      </div>
      {probabilities.length > 0 && <ProbabilityHistogram probabilities={probabilities} positive={run.threshold_positive} negative={run.threshold_negative} />}
      <div className="my-3"><KeepShareLine estimate={run.keep_share_estimate} actual={run.keep_share_actual} /></div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
        Latency p50 {percentile(latencies, 0.5) ?? '—'} ms · p95 {percentile(latencies, 0.95) ?? '—'} ms, over {latencies.length.toLocaleString()} rows.
        {run.length_correlation && <> Length correlation (Spearman, characters): {run.length_correlation.chars.rho?.toFixed(2) ?? run.length_correlation.chars.reason} on {run.length_correlation.chars.n} rows.</>}
      </p>
      {skipped.length > 0 && (
        <details className="mb-2"><summary className="text-sm cursor-pointer">Skipped rows ({skipped.length})</summary>
          <ul className="text-xs font-mono">{skipped.map((l) => <li key={l.row_key}>{l.row_key.slice(0, 12)} · {l.skip_reason}</li>)}</ul>
        </details>
      )}
      {parseFailures.length > 0 && (
        <details className="mb-2"><summary className="text-sm cursor-pointer">Parse failures ({parseFailures.length})</summary>
          <ul className="text-xs font-mono">{parseFailures.map((l) => <li key={l.row_key}>{l.row_key.slice(0, 12)} · {JSON.stringify(l.raw_output).slice(0, 200)}</li>)}</ul>
        </details>
      )}
      {run.state === 'completed' && (run.kind === 'classifier' || run.kind === 'rederived') && (
        <div className="flex flex-wrap items-end gap-2 my-3" data-testid="rederive">
          <label className="text-xs">Positive at or above<input aria-label="New positive threshold" className={`${field} block`} inputMode="decimal" value={pos} onChange={(e) => setPos(e.target.value)} /></label>
          <label className="text-xs">Negative at or below<input aria-label="New negative threshold" className={`${field} block`} inputMode="decimal" value={neg} onChange={(e) => setNeg(e.target.value)} /></label>
          <Button size="sm" variant="secondary" disabled={!rederiveValid} onClick={() => void rederiveRun(run.id, positive, negative)}>Re-derive labels</Button>
        </div>
      )}
      <ProvenanceBlock run={run} />
    </Card>
  );
}

// The run the Label step started, followed on the step itself (novice re-walk 2026-10-19,
// finding 4: Start jumped to Assemble with no progress). Rows done of total from the run's room
// (polling while the socket is down), the rate and time left while it runs, the result and the
// comparison with the data's own label column when it finishes. Continuing does not stop it.
import { useEffect } from 'react';

import { Button } from '@/components/common/Button';
import { useLabelRun } from '@/hooks/useLabelRun';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { formatDuration } from '@/utils/format';

import { AgreementBlock } from './AgreementBlock';
import { STATE_LABEL, rate } from './format';

export function RunProgress({ runId, onContinue, continueLabel }: { runId: string; onContinue?: () => void; continueLabel?: string }) {
  const run = useLabelRunsStore((s) => s.byId[runId]);
  const live = useLabelRunsStore((s) => s.live[runId]);
  const fetchRun = useLabelRunsStore((s) => s.fetchRun);
  useLabelRun(runId);

  useEffect(() => {
    void fetchRun(runId);
  }, [fetchRun, runId]);

  if (!run) return <p role="status" className="text-sm mb-3" data-testid="run-progress">Reading run {runId.slice(0, 12)}…</p>;
  const running = run.state === 'queued' || run.state === 'running';
  const done = running ? (live?.rows_done ?? run.rows_done) : run.rows_done;
  const total = live?.rows_total ?? run.rows_total;
  const fraction = total ? Math.min(1, done / total) : 0;
  const speed = running ? rate(live?.rows_per_second ?? null) : rate(run.timing?.rows_per_second ?? null);
  const eta = running && live?.eta_seconds != null ? `about ${formatDuration(live.eta_seconds)} left` : null;
  const counts = live?.counts ?? run.counts;
  return (
    <section className="mb-4 rounded-card border border-slate-200 dark:border-slate-700 p-3" data-testid="run-progress" aria-labelledby="run-progress-title">
      <h3 id="run-progress-title" className="font-semibold text-sm mb-1">
        Run {run.id.slice(0, 12)} · {STATE_LABEL[run.state] ?? run.state}
      </h3>
      <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden mb-1" role="progressbar" aria-valuenow={Math.round(fraction * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Rows labeled in this run">
        <div className={`h-full ${run.state === 'completed' ? 'bg-green-500' : 'bg-indigo-500'}`} style={{ width: `${fraction * 100}%` }} />
      </div>
      <p className="text-sm mb-2" data-testid="run-progress-rows">
        {done.toLocaleString()} of {total.toLocaleString()} rows labeled ({(fraction * 100).toFixed(1)}%)
        {speed ? ` · ${speed}` : ''}{eta ? ` · ${eta}` : ''}
        {!running && run.timing?.active_seconds != null ? ` · took ${formatDuration(run.timing.active_seconds)}` : ''}
      </p>
      {Object.keys(counts).length > 0 && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">{Object.entries(counts).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ')}</p>
      )}
      {run.error && <p role="alert" className="text-sm text-red-600 dark:text-red-400 mb-2">{run.error.message}</p>}
      {run.state === 'completed' && run.kind !== 'judge' && <AgreementBlock runId={run.id} state={run.state} />}
      {onContinue && (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={onContinue}>{continueLabel ?? 'Continue'}</Button>
          {running && <span className="text-xs text-slate-500 dark:text-slate-400">The run keeps going if you continue; Label runs follows it too.</span>}
        </div>
      )}
    </section>
  );
}

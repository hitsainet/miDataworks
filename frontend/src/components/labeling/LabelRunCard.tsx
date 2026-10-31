// One label run (FPRD 005 section 4.2): input version, a kind pill, model@revision on its endpoint
// (cyan when miLLM, R-03.59), the question or rubric, rows done of total, rows/s while measured (omitted otherwise), the state pill and
// the unpinned badge. Running bars use the indigo fill; completed bars are green.
import { Badge } from '@/components/common/Badge';
import type { LabelRun } from '@/types/labeling';
import type { LiveProgress } from '@/stores/labelRunsStore';

import { formatDuration } from '@/utils/format';

import { KIND_LABEL, STATE_LABEL, rate, runTimes } from './format';

export function LabelRunCard({ run, live, onOpen, selected }: { run: LabelRun; live?: LiveProgress; onOpen: () => void; selected: boolean }) {
  const s = run.endpoint_snapshot;
  // A live progress event counts only while the run goes, and never below the re-read run: once
  // it ends the run record is the truth (the last event can predate the final rows).
  const going = run.state === 'queued' || run.state === 'running';
  const done = going ? Math.max(live?.rows_done ?? 0, run.rows_done) : run.rows_done;
  const total = going ? (live?.rows_total ?? run.rows_total) : run.rows_total;
  const fraction = total ? Math.min(1, done / total) : 0;
  const millm = s.server_kind === 'millm' || run.kind === 'probe_verdict';
  const pinned = run.pinned ?? s.pinned ?? null;
  const subject = run.kind === 'probe_verdict'
    ? `probe ${s.probe?.probe_id ?? 'not reported'} · window ${s.probe?.window ?? 'not reported'}`
    : run.question ?? run.template_ref ?? '—';
  // Live rate while running; once stopped, the recorded working time and rate (finding 13).
  const measured = rate(live?.rows_per_second ?? (run.state === 'running' ? null : run.timing?.rows_per_second));
  const took = run.state !== 'running' && run.timing?.active_seconds != null ? `took ${formatDuration(run.timing.active_seconds)}` : null;
  const times = runTimes(run);
  const statePill = run.state === 'completed' ? 'success' : run.state === 'failed' ? 'danger' : run.state === 'running' ? 'primary' : 'default';
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="label-run-card"
      aria-pressed={selected}
      className={`w-full text-left rounded-card border p-4 bg-white dark:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-500 ${selected ? 'border-indigo-400' : 'border-slate-200 dark:border-slate-700'}`}
    >
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Badge variant="neutral">{KIND_LABEL[run.kind] ?? run.kind}</Badge>
        <Badge variant={statePill}>{STATE_LABEL[run.state] ?? run.state}</Badge>
        {pinned === false && <Badge variant="warning">Unpinned</Badge>}
        {run.revision_reported === false && <Badge variant="warning">Revision not reported</Badge>}
        {run.archived_at && <Badge variant="default">Archived</Badge>}
        <span className="text-xs text-slate-500 dark:text-slate-400" data-testid="run-version">
          {run.input_dataset_name ? `${run.input_dataset_name} v${run.input_version_number ?? '?'}` : <span className="font-mono">version {run.input_version_id.slice(0, 8)}</span>}
        </span>
      </div>
      <div className="text-sm mb-1">
        <span className={millm ? 'text-cyan-600 dark:text-cyan-400 font-medium' : 'font-medium'}>{String(s.model_id ?? '—')}</span>
        <span className="text-slate-500 dark:text-slate-400"> @ {String(s.model_revision ?? 'revision not reported').slice(0, 12)}</span>
      </div>
      <div className="text-sm text-slate-700 dark:text-slate-300 truncate mb-2">{subject}</div>
      <div className="h-1.5 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden mb-1" role="progressbar" aria-valuenow={Math.round(fraction * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Rows labeled">
        <div className={`h-full ${run.state === 'completed' ? 'bg-green-500' : 'bg-indigo-500'}`} style={{ width: `${fraction * 100}%` }} />
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400">
        {done.toLocaleString()} of {total.toLocaleString()} rows{took ? ` · ${took}` : ''}{measured ? ` · ${measured}` : ''}{run.row_filter?.max_rows ? ` · capped at ${run.row_filter.max_rows.toLocaleString()}` : ''} · chunks of {run.chunk_size}, resumable
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400" data-testid="run-times">
        Started {times.started} · Finished {times.finished}
      </div>
    </button>
  );
}

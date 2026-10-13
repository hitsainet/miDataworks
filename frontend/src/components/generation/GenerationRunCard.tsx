// One generation run (FPRD 007 section 4.2): mode, model (cyan: miLLM state), counts by outcome,
// pinned and revision badges. Running bars use the indigo fill; completed bars are green.
import { Badge } from '@/components/common/Badge';
import type { GenerationRun } from '@/types/generation';

import { STATE_LABEL } from './format';

export function GenerationRunCard({ run, live, selected, onOpen }: { run: GenerationRun; live?: Record<string, unknown>; selected: boolean; onOpen: () => void }) {
  const done = Number(live?.units_done ?? 0);
  const total = Number(live?.units_total ?? 0);
  const generated = (run.counts['respond:generated'] ?? 0) + (run.counts['expand:generated'] ?? 0);
  const discarded = (run.counts['respond:discarded'] ?? 0) + (run.counts['expand:discarded'] ?? 0);
  const fraction = run.state === 'completed' ? 1 : total ? Math.min(1, done / total) : 0;
  const pill = run.state === 'completed' ? 'success' : run.state === 'failed' ? 'danger' : run.state === 'running' ? 'primary' : 'default';
  const millm = run.server_kind === 'millm';
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-pressed={selected}
      data-testid="generation-run-card"
      className={`w-full text-left rounded-xl border p-4 bg-white dark:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-500 ${selected ? 'border-indigo-400' : 'border-slate-200 dark:border-slate-700'}`}
    >
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Badge variant="purple">{run.mode === 'steered_pairs' ? 'Steered pairs' : run.mode === 'minimal_pairs' ? 'Minimal pairs' : 'Standard'}</Badge>
        <Badge variant={pill}>{STATE_LABEL[run.state] ?? run.state}</Badge>
        {run.pinned === false && <Badge variant="warning">Unpinned</Badge>}
        {run.revision_reported === false && <Badge variant="warning">Revision not reported</Badge>}
        <span className="text-xs text-slate-500 dark:text-slate-400 font-mono">version {run.input_version_id.slice(0, 8)}</span>
      </div>
      <div className="text-sm mb-2">
        <span className={millm ? 'text-cyan-600 dark:text-cyan-400 font-medium' : 'font-medium'}>{String(run.generation_endpoint.model_id ?? '—')}</span>
        <span className="text-slate-500 dark:text-slate-400"> · {run.target_type} · {run.sample_size.toLocaleString()} seed rows × {run.n_responses}</span>
      </div>
      <div className="h-1.5 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden mb-1" role="progressbar" aria-label="Prompts done" aria-valuenow={Math.round(fraction * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${run.state === 'completed' ? 'bg-green-500' : 'bg-indigo-500'}`} style={{ width: `${fraction * 100}%` }} />
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400">
        {generated.toLocaleString()} generated · {discarded.toLocaleString()} discarded · {run.counts.pairs ?? 0} pairs
      </div>
    </button>
  );
}

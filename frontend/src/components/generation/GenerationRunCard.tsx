// One generation run, read at a glance (FPRD 007 section 4.2; operator request 2026-10-21): what it
// is for (kind, dataset by name, the behaviour), when (started, ended or running for, duration), how
// (model in cyan for miLLM state, template, seed rows), who, and the outcome (counts and the
// candidate version). Running bars use the indigo fill; completed bars are green. Ids stay secondary.
import { Badge } from '@/components/common/Badge';
import type { GenerationRun } from '@/types/generation';

import { STATE_LABEL } from './format';
import {
  NO_BEHAVIOUR,
  REVISION_TOOLTIP,
  behaviourLabel,
  behaviourSourceText,
  discardedCount,
  generatedCount,
  runTitle,
  seedText,
  templateText,
  topDiscardReasons,
  versionName,
  whenLine,
  whoText,
} from './runSummary';

export function GenerationRunCard({ run, live, selected, onOpen, now }: { run: GenerationRun; live?: Record<string, unknown>; selected: boolean; onOpen: () => void; now?: number }) {
  const done = Number(live?.units_done ?? 0);
  const total = Number(live?.units_total ?? 0);
  const generated = generatedCount(run);
  const discarded = discardedCount(run);
  const fraction = run.state === 'completed' ? 1 : total ? Math.min(1, done / total) : 0;
  const pill = run.state === 'completed' ? 'success' : run.state === 'failed' ? 'danger' : run.state === 'running' ? 'primary' : 'default';
  const millm = run.server_kind === 'millm';
  const behaviour = run.behaviour ?? null;
  const label = behaviourLabel(behaviour);
  const candidate = run.candidate_versions?.[run.candidate_versions.length - 1] ?? null;
  const pairVersion = run.minimal_pair_chain?.pair_version ?? null;
  const reasons = topDiscardReasons(run, 2);
  const template = templateText(run);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-pressed={selected}
      data-testid="generation-run-card"
      className={`w-full text-left rounded-xl border p-4 bg-white dark:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-500 ${selected ? 'border-indigo-400' : 'border-slate-200 dark:border-slate-700'}`}
    >
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <span className="font-medium text-sm text-slate-900 dark:text-slate-100" data-testid="run-title" title={`Input version id ${run.input_version_id}`}>{runTitle(run)}</span>
        <Badge variant={pill}>{STATE_LABEL[run.state] ?? run.state}</Badge>
        {run.pinned === false && <Badge variant="warning">Unpinned</Badge>}
        {run.archived_at && <Badge variant="default">Archived</Badge>}
      </div>
      <div className="text-xs mb-1" data-testid="run-behaviour">
        {label ? (
          <span title={behaviour ? `${behaviour.meaning ?? 'No positive meaning written'} (${behaviourSourceText(behaviour)})` : undefined}>
            <span className="text-slate-500 dark:text-slate-400">Behaviour: </span>
            <span className="font-medium text-slate-800 dark:text-slate-200">{label}</span>
            {behaviour?.key && behaviour.meaning ? <span className="text-slate-500 dark:text-slate-400"> — {behaviour.meaning}</span> : null}
            {behaviour && !behaviour.key ? <span className="text-slate-500 dark:text-slate-400"> (no behaviour key set)</span> : null}
          </span>
        ) : (
          <span className="text-slate-500 dark:text-slate-400">{NO_BEHAVIOUR}</span>
        )}
      </div>
      <div className="text-xs text-slate-600 dark:text-slate-300 mb-1" data-testid="run-when">{whenLine(run, now)}</div>
      <div className="text-xs mb-2">
        <span className={millm ? 'text-cyan-600 dark:text-cyan-400 font-medium' : 'font-medium'}>{String(run.generation_endpoint.model_id ?? 'model not recorded')}</span>
        {run.revision_reported === false && (
          <span className="text-slate-400 dark:text-slate-500" title={REVISION_TOOLTIP} data-testid="revision-not-reported"> (revision not reported)</span>
        )}
        <span className="text-slate-500 dark:text-slate-400">
          {template ? ` · ${template}` : ''} · {seedText(run)} · by {whoText(run)}
        </span>
      </div>
      <div className="h-1.5 rounded-sm bg-slate-200 dark:bg-slate-700 overflow-hidden mb-1" role="progressbar" aria-label="Prompts done" aria-valuenow={Math.round(fraction * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${run.state === 'completed' ? 'bg-green-500' : 'bg-indigo-500'}`} style={{ width: `${fraction * 100}%` }} />
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400" data-testid="run-outcome">
        {generated.toLocaleString()} generated · {discarded.toLocaleString()} discarded{reasons ? ` (${reasons})` : ''} · {run.counts.pairs ?? 0} pairs
        {run.state === 'completed' && (
          <> · {pairVersion ? <span title={`Version id ${pairVersion.id}`}>pairs in {versionName(pairVersion)}</span>
            : candidate ? <span title={`Version id ${candidate.id}`}>candidate {versionName(candidate)}</span>
            : run.minimal_pair_chain ? 'pair version still to come' : 'no candidate version yet'}</>
        )}
      </div>
    </button>
  );
}

// Results back from miStudio (FR-009.33 - FR-009.41): the rung in miStudio's own words, per-set
// AUROC with its interval and sample, firing rates, paired score or its reason, caveats verbatim.
import type { ProbeFigure, ResultsSnapshot } from '@/types/detectorSets';

import { ROLE_WORDS, auroc, count, firing, interval, pct } from './format';
import { RewardMarkBadge } from './RewardMarkBadge';

function Probe({ figure }: { figure: ProbeFigure }) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700/60 p-3" data-testid={`probe-${figure.probe_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          <span className="font-mono">{figure.probe_id}</span> · layer {figure.layer} · {figure.rule}
          {figure.selected ? ' · selected' : ''}
          {figure.gone ? ' · no longer in miStudio' : ''}
        </div>
        {figure.reward ? (
          <RewardMarkBadge />
        ) : (
          <span title={figure.rung_next_step ?? ''} className="rounded-full bg-emerald-100 dark:bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-800 dark:text-emerald-200" data-testid="rung-pill">
            miStudio · {figure.rung_language ?? `rung ${figure.rung}`}
          </span>
        )}
      </div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Threshold {figure.threshold.toFixed(2)} cut at {pct(figure.target_fpr)} false positive rate (realised {pct(figure.realised_fpr)})
      </div>
      {!figure.reward && (
        <table className="mt-2 w-full text-xs tabular-nums">
          <thead>
            <tr className="text-slate-500 dark:text-slate-400"><th className="text-left font-medium">Set</th><th className="text-right font-medium">AUROC [95% CI]</th><th className="text-right font-medium">n+ / n−</th><th className="text-right font-medium">Firing</th></tr>
          </thead>
          <tbody>
            {figure.sets.map((s) => (
              <tr key={s.probe_dataset_id} className="border-t border-slate-200 dark:border-slate-700/60">
                <td className="py-1">{s.role ? ROLE_WORDS[s.role] : ''} · {s.view_name}</td>
                <td className="text-right">{auroc(s.auroc)} {interval(s.ci)}</td>
                <td className="text-right">{count(s.n_positive)} / {count(s.n_negative)}</td>
                <td className="text-right">
                  {s.firing
                    ? s.firing.unreachable
                      ? 'unreachable'
                      : `${firing('positives', s.firing.positives_firing, s.firing.n_positive)}; ${firing('negatives', s.firing.negatives_firing, s.firing.n_negative)}`
                    : 'not reported'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {figure.sets.filter((s) => s.paired).map((s) => (
        <p key={`p-${s.probe_dataset_id}`} className="mt-1 text-xs" data-testid="paired-score">
          Paired score ({s.view_name}): {s.paired?.available ? `${pct(s.paired.paired)} ${interval(s.paired.ci)}, ${count(s.paired.pairs)} pairs` : s.paired?.reason}
        </p>
      ))}
      {figure.caveats.threshold_transfer_caution && <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">miStudio: {figure.caveats.threshold_transfer_caution}</p>}
      {figure.judge_runs.length > 0 && <p className="mt-1 text-xs">Compared with a judge on {figure.judge_runs.length} run(s).</p>}
    </div>
  );
}

export function ResultsPanel({ results, standalone = false }: { results: ResultsSnapshot | null; standalone?: boolean }) {
  if (!results) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-results">
        {standalone
          ? 'No results: results come back only from a configured miStudio.'
          : 'No results yet. After miStudio trains a probe on this set, press Refresh results.'}
      </p>
    );
  }
  return (
    <div className="space-y-3" data-testid="results-panel">
      <div className="text-xs text-slate-500 dark:text-slate-400">Read at {results.read_at} by {results.read_by}</div>
      {results.evaluations.map((f) => <Probe key={f.probe_id} figure={f} />)}
      {results.training_reward.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold">Training reward — not an evaluation</h3>
          {results.training_reward.map((f) => <Probe key={f.probe_id} figure={f} />)}
        </section>
      )}
    </div>
  );
}

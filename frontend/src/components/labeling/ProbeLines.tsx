// Probe-verdict runs (009): the probe a plan or run names, its bar for the window, the evidence
// rung in miStudio's own words, the one-input preflight, and the reproduction gate. A probe's bar is
// a probe SCORE, never a probability; AUROC is named as AUROC. Rung numbers are never mapped to
// words here: `rung_language` is shown verbatim.
import type { KeySummary, LabelRun, ProbeFacts, ProbePlan, Reproduction, ScoringForm, TargetChoice } from '@/types/labeling';

const num = (x: number | null | undefined, digits = 3) => (x === null || x === undefined ? 'not reported' : x.toFixed(digits));
const said = (x: unknown) => (x === null || x === undefined || x === '' ? 'not reported' : String(x));

/** The window's own bar, named as the window's (a verdict may be judged at a length band instead). */
export function barText(bar: ProbeFacts['window_bar'], window?: string): string {
  const name = window ? `window ${window} bar` : 'window bar';
  const value = bar.threshold === null ? `no ${name} reported` : `${name} ${num(bar.threshold)} (probe score, not a probability)`;
  return bar.provisional ? `${value} · provisional: this window has no bar of its own` : value;
}

export function ProbePlanLine({ probe }: { probe: ProbePlan }) {
  const p = probe.preflight;
  let preflight: string;
  if (!p.checked) preflight = `Preflight not checked: ${said(p.reason)}`;
  else if (p.error) preflight = `Preflight failed: ${said((p.error as { message?: unknown }).message ?? JSON.stringify(p.error))}`;
  else {
    const verdict = p.verdict === true ? 'fires' : p.verdict === false ? 'silent' : 'no verdict';
    // Which bar the number is (a length band, or the window's own): never a bare "bar" beside the window bar.
    const against = p.bar ? p.bar.label : `bar ${num(p.threshold)} (which bar it is was not reported)`;
    preflight = `Preflight on one row (${said(p.row_key).slice(0, 12)}): score ${num(p.score)} against ${against} → ${verdict}${p.provisional ? ' (provisional)' : ''}`;
  }
  return (
    <div className="text-sm mb-3 space-y-0.5" data-testid="probe-plan-line">
      <p>
        Probe <span className="font-medium text-cyan-600 dark:text-cyan-400">{probe.name}</span>{' '}
        <span className="font-mono text-slate-500 dark:text-slate-400">({probe.probe_id})</span> · layer {said(probe.layer)} · fitted on {said(probe.hf_id)} · window {probe.window} · {barText(probe.window_bar, probe.window)}
      </p>
      <p className="text-slate-600 dark:text-slate-300">Evidence: {said(probe.rung_language)}</p>
      <p className="text-slate-600 dark:text-slate-300" data-testid="probe-preflight">{preflight}</p>
    </div>
  );
}

/** Rows and distinct inputs: the same two numbers the run and the link state. */
export function keysText(k: KeySummary): string {
  const copies = k.rows === k.row_keys ? 'every row a distinct input' : `${k.row_keys.toLocaleString()} distinct inputs, each scored once (${k.keys_with_copies.toLocaleString()} appear in several rows)`;
  return `${k.rows.toLocaleString()} rows, ${copies}`;
}

/** Keys whose copies carry both classes: shown, never resolved. */
export function conflictText(k: KeySummary): string | null {
  const c = k.conflicting;
  if (!c.count) return null;
  const first = c.keys[0];
  return `${c.count.toLocaleString()} input(s) appear in several rows labelled BOTH positive and negative (${c.rows.toLocaleString()} rows; e.g. ${first.row_key.slice(0, 12)}: ${first.positive} positive, ${first.negative} negative). Each copy keeps its own label, as miStudio counted it; check whether those rows are really the same input.`;
}

export function reproductionText(r: Reproduction): string {
  const [lo, hi] = r.mistudio_ci;
  const interval = `[${num(lo)}, ${num(hi)}]`;
  if (r.state === 'failed' && r.failed_run_id) {
    return `The same reproduction check already failed on run ${r.failed_run_id}: miLLM AUROC ${num(r.millm_auroc)} against miStudio's AUROC ${num(r.mistudio_auroc)}, interval ${interval}. ${r.retry ?? ''}`.trim();
  }
  if (r.state === 'will_run') {
    const counted = r.row_keys ? keysText(r.row_keys) : `${r.n_rows.toLocaleString()} rows`;
    const retry = r.retry_of ? ` Retrying after run ${r.retry_of.run_id} failed, because: ${r.retry_of.reason}` : '';
    return `Before labeling, scores ${counted} of ${r.view_name ?? r.role} and requires an AUROC inside miStudio's ${interval} (miStudio reported AUROC ${num(r.mistudio_auroc)}).${retry}`;
  }
  if (r.cached_from_run_id) {
    return `Reproduction already passed on run ${r.cached_from_run_id}${r.millm_auroc != null ? ` (miLLM AUROC ${num(r.millm_auroc)}, miStudio ${num(r.mistudio_auroc)}, interval ${interval})` : ''}.`;
  }
  const scored = r.rows_scored != null ? ` on ${r.rows_scored.toLocaleString()} rows${r.rows_dropped ? ` (${r.rows_dropped.toLocaleString()} dropped)` : ''}` : '';
  return `Reproduction ${r.state}: miLLM AUROC ${num(r.millm_auroc)}${scored} against miStudio's AUROC ${num(r.mistudio_auroc)}, interval ${interval}${r.reason ? `. ${r.reason}` : ''}.`;
}

const AGREEMENT_VERDICT: Record<ScoringForm['agreement'], string> = {
  differs: 'These DIFFER',
  equal_by_render_rule: 'Equal by render rule, token ids not compared',
  not_verified: 'Not verified equal',
};

export function scoringFormText(form: ScoringForm): string {
  const verdict = AGREEMENT_VERDICT[form.agreement] ?? 'Not verified equal';
  return `miStudio scored: ${form.mistudio.described_as}, scope ${form.mistudio.scope}. miLLM scores: ${form.millm.described_as}. ${verdict}: ${form.reason}`;
}

export function ScoringFormLine({ form }: { form: ScoringForm }) {
  const tone = form.agreement === 'differs' ? 'text-amber-700 dark:text-amber-400' : 'text-slate-600 dark:text-slate-300';
  return (
    <div data-testid="scoring-form" data-agreement={form.agreement}>
      <p className={`text-xs ${tone}`}>{scoringFormText(form)}</p>
      {form.note && <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="scoring-form-note">Note: {form.note}</p>}
    </div>
  );
}

/** Why this target was chosen over the other recorded evaluations, with their check levels. */
export function choiceText(c: TargetChoice): string {
  const level = (l: string | null) => (l === 'content' ? 'content hash' : l === 'counts_only' ? 'counts only' : 'snapshot');
  const alts = c.alternatives.map((a) => `${a.link_id ?? a.snapshot_id ?? a.source} (${level(a.check_level)}, ${a.role})`);
  const more = c.alternatives_total > c.alternatives.length ? ` and ${c.alternatives_total - c.alternatives.length} more` : '';
  return `Chosen because ${c.why}.${alts.length ? ` Not chosen: ${alts.join(', ')}${more}.` : ' No other recorded evaluation.'}`;
}

/** Where the gate's target came from. A link says how its rows were checked, never more. */
export function targetText(r: Reproduction): string {
  if (r.source === 'linked_mistudio_evaluation') {
    const checked = r.link_check_level === 'content' ? 'rows checked by content hash' : 'counts only: row content was not compared';
    return `Target: miStudio's recorded evaluation on ${r.view_name ?? r.probe_dataset_id}, through reproduction link ${r.link_id} (${checked}).`;
  }
  return `Target: the results snapshot ${r.snapshot_id ?? 'not reported'} of this detector set's send.`;
}

export function ReproductionLine({ reproduction }: { reproduction: Reproduction }) {
  const tone = reproduction.state === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-slate-700 dark:text-slate-300';
  return (
    <div className="mb-3 space-y-0.5">
      <p className={`text-sm ${tone}`} data-testid="reproduction-line" data-state={reproduction.state}>{reproductionText(reproduction)}</p>
      <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="reproduction-target" data-source={reproduction.source ?? 'detector_results'}>{targetText(reproduction)}</p>
      {reproduction.choice && <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="reproduction-choice">{choiceText(reproduction.choice)}</p>}
      {reproduction.row_keys && reproduction.state !== 'will_run' && <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="reproduction-keys">Counted: {keysText(reproduction.row_keys)}.</p>}
      {reproduction.row_keys && conflictText(reproduction.row_keys) && <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="reproduction-conflicts">{conflictText(reproduction.row_keys)}</p>}
      {reproduction.scoring_form && <ScoringFormLine form={reproduction.scoring_form} />}
    </div>
  );
}

/** A probe-verdict run's own record: the probe and window, pinned or not, and the reproduction. */
export function ProbeRunBlock({ run }: { run: LabelRun }) {
  const s = run.endpoint_snapshot;
  const pinned = s.pinned ?? run.pinned;
  return (
    <div className="mb-3 rounded border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="probe-run-block">
      <p className="text-sm">
        Probe <span className="font-mono text-cyan-600 dark:text-cyan-400">{s.probe?.probe_id ?? 'not reported'}</span> · window {s.probe?.window ?? 'not reported'}
        {s.probe && <> · {barText(s.probe.window_bar, s.probe.window)}</>}
      </p>
      {s.probe && <p className="text-sm text-slate-600 dark:text-slate-300">Evidence: {said(s.probe.rung_language)}</p>}
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {pinned === true ? 'Pinned under the miLLM lease' : pinned === false ? `Unpinned: ${said(s.unpinned_reason)}` : 'Pinned: not known yet'}
      </p>
      {s.reproduction && <ReproductionLine reproduction={s.reproduction} />}
    </div>
  );
}

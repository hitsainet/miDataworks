// The guided flow's Label step (FR-005.50; FPRD 005 sections 2.4 and 4.2). 002 owns the flow and
// mounts this through guidedStep.label.tsx (P-23). Thresholds start EMPTY and are required (T-19);
// the start button stays disabled until both are valid and names the action and the row count.
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useHealthStore } from '@/stores/healthStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { LabelRole, LabelRunStart, ProbeWindow } from '@/types/labeling';

import { CalibrationCallout } from './CalibrationCallout';
import { coverageText } from './format';
import { KeepShareLine } from './KeepShareLine';
import { ReproductionRefusalBlock } from './LinkEvaluation';
import { ProbePlanLine, ReproductionLine } from './ProbeLines';
import { RubricLibrary } from './RubricLibrary';
import { SamplePanel } from './SamplePanel';

export const PLAN_DEBOUNCE_MS = 500;
export const QUESTION_HINT = 'Asked of every row exactly as written. Wording changes what is measured.';
export const PIN_HINT = 'The job pins the model it starts with and refuses to run if it changes.';
export const WINDOW_HINT = "The all window reproduces miStudio's own scope; the others read part of each row.";
const WINDOWS: ProbeWindow[] = ['all', 'prompt', 'response', 'last_user'];

/** Client-side mirror of the server's threshold rule (T-19); the server re-validates. */
export function thresholdsValid(positive: string, negative: string): boolean {
  if (positive.trim() === '' || negative.trim() === '') return false;
  const p = Number(positive);
  const n = Number(negative);
  return Number.isFinite(p) && Number.isFinite(n) && n >= 0 && p <= 1 && n < p;
}

export function LabelStep({ versionId, onStarted }: { versionId?: string | null; onStarted?: () => void }) {
  const templates = useLabelingStore((s) => s.templates);
  const rubrics = useLabelingStore((s) => s.rubrics);
  const sample = useLabelingStore((s) => s.sample);
  const sampleBusy = useLabelingStore((s) => s.sampleBusy);
  const keepShare = useLabelingStore((s) => s.keepShare);
  const plan = useLabelingStore((s) => s.plan);
  const planError = useLabelingStore((s) => s.planError);
  const approval = useLabelingStore((s) => s.approval);
  const started = useLabelingStore((s) => s.started);
  const error = useLabelingStore((s) => s.error);
  const calibration = useLabelingStore((s) => s.calibration);
  const fetchTemplates = useLabelingStore((s) => s.fetchTemplates);
  const fetchRubrics = useLabelingStore((s) => s.fetchRubrics);
  const probes = useLabelingStore((s) => s.probes);
  const probesError = useLabelingStore((s) => s.probesError);
  const probesUnconfiguredByList = useLabelingStore((s) => s.probesUnconfigured);
  // The top bar's health read says whether miLLM is configured at all (miDataworks works without
  // it); the probe list's own 409 PROBE_ENDPOINT_UNCONFIGURED says the same from the other side.
  const millmDep = useHealthStore((s) => s.health?.dependencies.millm);
  const healthSaysUnconfigured = millmDep?.configured === false;
  const probesUnconfigured = probesUnconfiguredByList || healthSaysUnconfigured;
  const planRefusal = useLabelingStore((s) => s.planRefusal);
  const fetchProbes = useLabelingStore((s) => s.fetchProbes);
  const runSample = useLabelingStore((s) => s.runSample);
  const startKeepShare = useLabelingStore((s) => s.startKeepShare);
  const pollKeepShare = useLabelingStore((s) => s.pollKeepShare);
  const fetchPlan = useLabelingStore((s) => s.fetchPlan);
  const startRun = useLabelingStore((s) => s.startRun);
  const fetchCalibration = useLabelingStore((s) => s.fetchCalibration);
  const versionList = useVersionsStore((s) => s.versionList);
  const fetchVersionList = useVersionsStore((s) => s.fetchVersionList);

  const [role, setRole] = useState<LabelRole>('classifier');
  const [version, setVersion] = useState(versionId ?? '');
  const [templateId, setTemplateId] = useState('');
  const [rubricId, setRubricId] = useState('');
  const [showRubrics, setShowRubrics] = useState(false);
  const [question, setQuestion] = useState('');
  const [column, setColumn] = useState('text');
  const [positive, setPositive] = useState('');
  const [negative, setNegative] = useState('');
  const [probeId, setProbeId] = useState('');
  const [window_, setWindow] = useState<ProbeWindow>('all');
  const [columnHolds, setColumnHolds] = useState<'text' | 'messages'>('text');
  // A deliberate retry of a reproduction check that already failed (finding 3): the reason is sent,
  // recorded on the run, and only then does the plan read "will run" again.
  const [retryDraft, setRetryDraft] = useState('');
  const [retryReason, setRetryReason] = useState('');
  const isProbe = role === 'probe';

  useEffect(() => {
    void fetchTemplates();
    void fetchRubrics();
    void fetchVersionList();
  }, [fetchTemplates, fetchRubrics, fetchVersionList]);

  useEffect(() => {
    // Read once on mount too, so the "Label with" option can say why probes are unavailable
    // (miLLM not configured) before anyone chooses it. miDataworks works without miLLM.
    void fetchProbes();
  }, [fetchProbes]);

  useEffect(() => {
    if (isProbe) void fetchProbes();
  }, [isProbe, fetchProbes]);

  const chooseRole = (next: LabelRole) => {
    // A plan describes one labeler; never show the last role's plan under the new one.
    useLabelingStore.setState({ plan: null, planError: null, planRefusal: null, sample: null, link: null, linkError: null, linkApproval: null });
    setRole(next);
  };

  const template = templates.find((t) => t.id === templateId);
  const rubric = rubrics.find((r) => r.id === rubricId);
  const fields = useMemo(
    () => (role === 'classifier' ? template?.body.input_fields : rubric?.body.input_fields) ?? [],
    [role, template, rubric],
  );
  const valid =
    Boolean(version) &&
    (isProbe
      ? Boolean(probeId) && column.trim() !== ''
      : role === 'classifier' ? Boolean(templateId) && question.trim() !== '' && thresholdsValid(positive, negative) : Boolean(rubricId));

  const body: LabelRunStart = useMemo(
    () => isProbe ? {
      // A probe-verdict run: the probe's own bar decides, so no template, question or thresholds.
      input_version_id: version,
      role: 'probe',
      probe: { probe_id: probeId, window: window_ },
      field_map: { [columnHolds]: column.trim() },
      ...(retryReason.trim() ? { reproduction_retry_reason: retryReason.trim() } : {}),
    } : ({
      input_version_id: version,
      role,
      template_id: role === 'classifier' ? templateId : null,
      rubric_id: role === 'judge' ? rubricId : null,
      question: question.trim() || null,
      field_map: Object.fromEntries(fields.map((f) => [f, column])),
      threshold_positive: role === 'classifier' && positive !== '' ? Number(positive) : null,
      threshold_negative: role === 'classifier' && negative !== '' ? Number(negative) : null,
    }),
    [isProbe, probeId, window_, columnHolds, retryReason, version, role, templateId, rubricId, question, fields, column, positive, negative],
  );

  useEffect(() => {
    if (!valid) return undefined;
    const timer = setTimeout(() => void fetchPlan(body), PLAN_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [valid, body, fetchPlan]);

  useEffect(() => {
    if (plan) void fetchCalibration(plan.labeler_identity_hash);
  }, [plan, fetchCalibration]);

  const keepRunning = keepShare && !['completed', 'failed'].includes(keepShare.status);
  useEffect(() => {
    if (!keepRunning) return undefined;
    const timer = setInterval(() => void pollKeepShare(), 1500);
    return () => clearInterval(timer);
  }, [keepRunning, pollKeepShare]);

  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  const rows = plan?.rows_to_score ?? 0;
  const gateFailed = isProbe && plan?.reproduction?.state === 'failed';
  const estimate = keepShare?.status === 'completed' && keepShare.share !== null
    ? { share: keepShare.share, lo: keepShare.lo ?? 0, hi: keepShare.hi ?? 0, n: keepShare.n ?? 0 }
    : null;

  return (
    <div data-testid="label-step">
      <h2 className="font-semibold mb-1">Label the rows</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">{PIN_HINT}</p>
      <div className="grid gap-3 sm:grid-cols-2 mb-3">
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Version to label</span>
          <select aria-label="Version to label" value={version} onChange={(e) => setVersion(e.target.value)} className={field}>
            <option value="">Choose a version</option>
            {versionList.map((v) => <option key={v.id} value={v.id}>{v.dataset_name} v{v.number} ({v.total_rows.toLocaleString()} rows)</option>)}
          </select>
        </label>
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Label with</span>
          <select aria-label="Label with" value={role} onChange={(e) => chooseRole(e.target.value as LabelRole)} className={field}>
            <option value="classifier">The classifier</option>
            <option value="judge">The judge</option>
            <option value="probe" disabled={healthSaysUnconfigured && role !== 'probe'}>{probesUnconfigured ? 'A probe in miLLM (miLLM not configured)' : 'A probe in miLLM'}</option>
          </select>
        </label>
        {isProbe ? (
          <>
            <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Probe</span>
              <select aria-label="Probe" value={probeId} onChange={(e) => setProbeId(e.target.value)} className={field}>
                <option value="">
                  {probesError
                    ? `Probes unavailable: ${probesUnconfigured ? 'miLLM is not configured (MILLM_BASE_URL)' : probesError}`
                    : probes ? (probes.items.length ? 'Choose a probe' : 'No probes imported in miLLM') : 'Loading probes from miLLM'}
                </option>
                {(probes?.items ?? []).map((p) => (
                  <option key={p.probe_id} value={p.probe_id}>
                    {p.name} · layer {p.layer} · fitted on {p.hf_id}
                    {p.fits_resident_model === false && ` (fitted on ${p.hf_id} — not the loaded model)`}
                    {p.fits_resident_model === null && ' (no model loaded in miLLM)'}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Window</span>
              <select aria-label="Window" value={window_} onChange={(e) => setWindow(e.target.value as ProbeWindow)} className={field}>
                {WINDOWS.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
              <span className="block mt-1 text-slate-500 dark:text-slate-400">{WINDOW_HINT}</span>
            </label>
            <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">The column holds</span>
              <select aria-label="The column holds" value={columnHolds} onChange={(e) => setColumnHolds(e.target.value as 'text' | 'messages')} className={field}>
                <option value="text">Plain text, read as one user turn</option>
                <option value="messages">A chat (messages)</option>
              </select>
            </label>
          </>
        ) : role === 'classifier' ? (
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Decision template</span>
            <select aria-label="Decision template" value={templateId} onChange={(e) => setTemplateId(e.target.value)} className={field}>
              <option value="">Choose a template</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.ref} ({t.bound_model_id ?? 'any model'})</option>)}
            </select>
          </label>
        ) : (
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Rubric</span>
            <select aria-label="Rubric" value={rubricId} onChange={(e) => setRubricId(e.target.value)} className={field}>
              <option value="">Choose a rubric</option>
              {rubrics.map((r) => <option key={r.id} value={r.id}>{r.ref} ({r.style})</option>)}
            </select>
            <button type="button" className="mt-1 text-indigo-600 dark:text-indigo-400 hover:underline" aria-expanded={showRubrics} onClick={() => setShowRubrics((v) => !v)}>
              {showRubrics ? 'Hide the rubric library' : 'Write, import or clone a rubric'}
            </button>
          </label>
        )}
        {isProbe ? (
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Column the probe reads</span>
            <input aria-label="Column the probe reads" value={column} onChange={(e) => setColumn(e.target.value)} className={`${field} font-mono`} />
            <span className="block mt-1 text-slate-500 dark:text-slate-400">
              {columnHolds === 'text' ? 'Each row is sent to miLLM as one user turn.' : 'Each row is sent to miLLM as the chat it holds.'}
            </span>
          </label>
        ) : (
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Column the template reads</span>
            <input aria-label="Column the template reads" value={column} onChange={(e) => setColumn(e.target.value)} className={`${field} font-mono`} />
          </label>
        )}
      </div>
      {role === 'judge' && showRubrics && (
        <div className="mb-4 rounded-xl border border-slate-200 dark:border-slate-700 p-3">
          <RubricLibrary onSaved={(r) => setRubricId(r.id)} />
        </div>
      )}
      {isProbe && probesError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400 mb-3" data-testid="probes-unavailable">
          {probesUnconfigured ? `Probe verdicts need miLLM, and it is not configured: ${probesError} Every other labeler works without it.` : probesError}
        </p>
      )}
      {!isProbe && (
        <label className="block text-xs mb-3"><span className="block text-slate-500 dark:text-slate-400 mb-1">Question</span>
          <textarea aria-label="Question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} className={field} />
          <span className="block mt-1 text-slate-500 dark:text-slate-400">{QUESTION_HINT}</span>
        </label>
      )}
      {role === 'classifier' && (
        <div className="grid gap-3 sm:grid-cols-2 mb-3">
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Positive at or above (probability, 0 to 1)</span>
            <input aria-label="Positive at or above" inputMode="decimal" value={positive} onChange={(e) => setPositive(e.target.value)} className={`${field} font-mono`} />
          </label>
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Negative at or below (probability, 0 to 1)</span>
            <input aria-label="Negative at or below" inputMode="decimal" value={negative} onChange={(e) => setNegative(e.target.value)} className={`${field} font-mono`} />
          </label>
          {(positive !== '' || negative !== '') && !thresholdsValid(positive, negative) && (
            <p role="alert" className="sm:col-span-2 text-xs text-red-600 dark:text-red-400">Set both thresholds between 0 and 1, with the negative threshold lower than the positive one.</p>
          )}
        </div>
      )}
      {!isProbe && (
        <div className="flex flex-wrap gap-2 mb-3">
          <Button variant="secondary" disabled={!valid || sampleBusy} loading={sampleBusy} onClick={() => void runSample(body)}>Try it on a sample</Button>
          {role === 'classifier' && <Button variant="secondary" disabled={!valid || Boolean(keepRunning)} onClick={() => void startKeepShare(body)}>Estimate keep share</Button>}
        </div>
      )}
      <div className="space-y-3 mb-4">
        {!isProbe && <SamplePanel sample={sample} />}
        {role === 'classifier' && <KeepShareLine estimate={estimate} />}
        {plan && <CalibrationCallout status={calibration[plan.labeler_identity_hash]} />}
      </div>
      {plan && (
        <p className="text-sm mb-3" data-testid="plan-line">
          {coverageText(plan.rows_total, plan.row_coverage)} · {plan.rows_reused.toLocaleString()} already labeled by this labeler and reused · {plan.rows_to_score.toLocaleString()} to label
          {plan.approval_needed && ' · an agent start of this size waits for your approval'}
        </p>
      )}
      {plan?.row_coverage?.copies_disagree && (
        <p className="text-sm text-amber-700 dark:text-amber-400 mb-3" data-testid="plan-copies-disagree">
          {plan.row_coverage.copies_disagree.message} One label per input applies to every copy.
        </p>
      )}
      {plan?.probe && <ProbePlanLine probe={plan.probe} />}
      {plan?.reproduction && <ReproductionLine reproduction={plan.reproduction} />}
      {gateFailed && (
        <div className="mb-3 space-y-2" data-testid="retry-anyway">
          <label className="block text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Why run the same check again? (recorded on the run)</span>
            <textarea aria-label="Reason to retry the reproduction check" value={retryDraft} onChange={(e) => setRetryDraft(e.target.value)} rows={2} className={field} />
          </label>
          <Button variant="secondary" disabled={retryDraft.trim() === ''} onClick={() => setRetryReason(retryDraft.trim())}>Retry the check anyway</Button>
        </div>
      )}
      {isProbe && planRefusal && <ReproductionRefusalBlock refusal={planRefusal} onLinked={() => void fetchPlan(body)} />}
      {planError && !(isProbe && planRefusal) && <p role="alert" className="text-sm text-red-600 dark:text-red-400 mb-3">{planError}</p>}
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400 mb-3">{error}</p>}
      {approval && <p role="status" className="text-sm mb-3">Waiting for the operator to approve this run ({approval.approval_id}).</p>}
      {started && <p role="status" className="text-sm mb-3">Run {started.id.slice(0, 12)} started; follow it on Label runs.</p>}
      <Button disabled={!valid || !plan || gateFailed} onClick={() => void startRun(body).then((run) => (run ? onStarted?.() : undefined))}>
        Label {rows.toLocaleString()} rows
      </Button>
    </div>
  );
}

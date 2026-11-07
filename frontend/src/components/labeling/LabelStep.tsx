// The guided flow's Label step (FR-005.50; FPRD 005 sections 2.4 and 4.2). 002 owns the flow and
// mounts this through guidedStep.label.tsx (P-23). Thresholds start EMPTY and are required (T-19);
// the start button stays disabled until both are valid and names the action and the row count.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useHealthStore } from '@/stores/healthStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { LabelRole, LabelRun, LabelRunStart, ProbeWindow } from '@/types/labeling';
import type { VersionSummary } from '@/types/versions';

import { CalibrationCallout } from './CalibrationCallout';
import { coverageText } from './format';
import { KeepShareLine } from './KeepShareLine';
import { ReproductionRefusalBlock } from './LinkEvaluation';
import { groupSize, probeFactLines, probeOptionText, probeTags } from './probeChoice';
import { ProbePlanLine, ProbeSummary, ReproductionLine } from './ProbeLines';
import { RubricLibrary } from './RubricLibrary';
import { RunProgress } from './RunProgress';
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

export { probeOptionText } from './probeChoice';

/** How often the step re-reads miLLM while it waits for a model to be loaded there (re-walk
 * finding 1: the step kept saying "No model is loaded" after the model was loaded). */
export const RESIDENT_POLL_MS = 5000;
/** Plan refusals that a model load in miLLM clears. */
export const MODEL_WAIT_CODES = ['MODEL_NOT_LOADED', 'MODEL_NOT_RESIDENT'];

/** The labeler choices the guided flow keeps on its draft, so a reload does not lose them. */
export interface LabelChoices {
  role?: LabelRole;
  probe_id?: string;
  window?: ProbeWindow;
  column?: string;
  column_holds?: 'text' | 'messages';
  row_cap?: string;
  template_id?: string;
  rubric_id?: string;
  question?: string;
  positive?: string;
  negative?: string;
}

/** A dataset name that reads as a test or scratch build (hidden from the version list by default). */
export const TEST_NAME = /(^|[^a-z0-9])(test|tests|e2e|smoke|scratch|tmp)([^a-z0-9]|$)/i;

/** Versions shown by default: each dataset's newest built version, not test builds (finding 11). */
export function isEverydayVersion(v: Pick<VersionSummary, 'is_head' | 'dataset_name'>): boolean {
  return v.is_head !== false && !TEST_NAME.test(v.dataset_name);
}

/** A row cap typed by the user: a whole number above 0, or null (no cap). */
export function parseRowCap(text: string): number | null | 'invalid' {
  const t = text.replace(/[,_\s]/g, '');
  if (t === '') return null;
  const n = Number(t);
  return Number.isInteger(n) && n >= 1 ? n : 'invalid';
}

export function LabelStep({
  versionId,
  onStarted,
  initial,
  onChoicesChange,
  runId,
  onContinue,
}: {
  versionId?: string | null;
  onStarted?: (run: LabelRun) => void;
  /** Choices kept on the guided draft; the step starts from them. */
  initial?: LabelChoices;
  onChoicesChange?: (choices: LabelChoices) => void;
  /** The run this journey started: followed here until the user continues. */
  runId?: string | null;
  onContinue?: () => void;
}) {
  const templates = useLabelingStore((s) => s.templates);
  const rubrics = useLabelingStore((s) => s.rubrics);
  const sample = useLabelingStore((s) => s.sample);
  const sampleBusy = useLabelingStore((s) => s.sampleBusy);
  const keepShare = useLabelingStore((s) => s.keepShare);
  const plan = useLabelingStore((s) => s.plan);
  const planError = useLabelingStore((s) => s.planError);
  const planErrorCode = useLabelingStore((s) => s.planErrorCode);
  const planErrorDetails = useLabelingStore((s) => s.planErrorDetails);
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
  const millmUi = millmDep?.ui_url ? millmDep.ui_url.replace(/\/+$/, '') : null;
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
  const followedState = useLabelRunsStore((s) => (runId ? s.byId[runId]?.state : undefined));
  const followedLive = followedState === 'queued' || followedState === 'running';
  const versionList = useVersionsStore((s) => s.versionList);
  const fetchVersionList = useVersionsStore((s) => s.fetchVersionList);

  const [role, setRole] = useState<LabelRole>(initial?.role ?? 'classifier');
  const [version, setVersion] = useState(versionId ?? '');
  // The journey's version arrives after a build; it is chosen unless the user chose another.
  const [chosenByUser, setChosenByUser] = useState(false);
  if (versionId && !chosenByUser && version !== versionId) setVersion(versionId);
  const [versionQuery, setVersionQuery] = useState('');
  const [rowCap, setRowCap] = useState(initial?.row_cap ?? '');
  const [showAllVersions, setShowAllVersions] = useState(false);
  const cap = parseRowCap(rowCap);
  const journeyVersion = useVersionsStore((s) => (versionId ? s.versions[versionId] : undefined));
  const [templateId, setTemplateId] = useState(initial?.template_id ?? '');
  const [rubricId, setRubricId] = useState(initial?.rubric_id ?? '');
  const [showRubrics, setShowRubrics] = useState(false);
  const [question, setQuestion] = useState(initial?.question ?? '');
  const [column, setColumn] = useState(initial?.column ?? 'text');
  const [positive, setPositive] = useState(initial?.positive ?? '');
  const [negative, setNegative] = useState(initial?.negative ?? '');
  const [probeId, setProbeId] = useState(initial?.probe_id ?? '');
  const [window_, setWindow] = useState<ProbeWindow>(initial?.window ?? 'all');
  const [columnHolds, setColumnHolds] = useState<'text' | 'messages'>(initial?.column_holds ?? 'text');
  // A deliberate retry of a reproduction check that already failed (finding 3): the reason is sent,
  // recorded on the run, and only then does the plan read "will run" again.
  const [retryDraft, setRetryDraft] = useState('');
  const [retryReason, setRetryReason] = useState('');
  // One start per click, as one import per click (re-walk finding 5's class of defect).
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const isProbe = role === 'probe';

  // The plan request as it stands, for a re-check started by a timer or by focus.
  const latestBody = useRef<{ valid: boolean; body: LabelRunStart } | null>(null);

  // Every choice goes to the guided draft as it changes (re-walk finding 1: a reload reset the
  // labeler to "The classifier" and lost the probe).
  const choices: LabelChoices = useMemo(
    () => ({ role, probe_id: probeId, window: window_, column, column_holds: columnHolds, row_cap: rowCap, template_id: templateId, rubric_id: rubricId, question, positive, negative }),
    [role, probeId, window_, column, columnHolds, rowCap, templateId, rubricId, question, positive, negative],
  );
  const choicesKey = JSON.stringify(choices);
  const savedKey = useRef(choicesKey);
  useEffect(() => {
    if (choicesKey === savedKey.current) return;
    savedKey.current = choicesKey;
    onChoicesChange?.(JSON.parse(choicesKey) as LabelChoices);
  }, [choicesKey, onChoicesChange]);

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
    Boolean(version) && cap !== 'invalid' &&
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
      ...(typeof cap === 'number' ? { row_filter: { max_rows: cap } } : {}),
    } : ({
      input_version_id: version,
      role,
      template_id: role === 'classifier' ? templateId : null,
      rubric_id: role === 'judge' ? rubricId : null,
      question: question.trim() || null,
      field_map: Object.fromEntries(fields.map((f) => [f, column])),
      threshold_positive: role === 'classifier' && positive !== '' ? Number(positive) : null,
      threshold_negative: role === 'classifier' && negative !== '' ? Number(negative) : null,
      ...(typeof cap === 'number' ? { row_filter: { max_rows: cap } } : {}),
    }),
    [isProbe, probeId, window_, columnHolds, retryReason, version, role, templateId, rubricId, question, fields, column, positive, negative, cap],
  );

  useEffect(() => {
    if (!valid) return undefined;
    const timer = setTimeout(() => void fetchPlan(body), PLAN_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [valid, body, fetchPlan]);
  useEffect(() => {
    latestBody.current = { valid, body };
  }, [valid, body]);
  // When the run this step follows stops, count again: its rows are now labels to reuse.
  useEffect(() => {
    if (followedState && !['queued', 'running'].includes(followedState) && latestBody.current?.valid) void fetchPlan(latestBody.current.body);
  }, [followedState, fetchPlan]);

  useEffect(() => {
    if (plan) void fetchCalibration(plan.labeler_identity_hash);
  }, [plan, fetchCalibration]);

  // Waiting for a model in miLLM: the plan was refused because the model is not loaded, or the
  // probe list says nothing is loaded. Re-read miLLM every RESIDENT_POLL_MS, whenever the tab
  // comes back into view, and on "Check again", so a load done in miLLM's own UI shows up here
  // without a reload (re-walk finding 1).
  const waitingForModel =
    (planErrorCode !== null && MODEL_WAIT_CODES.includes(planErrorCode)) ||
    (isProbe && probes !== null && probes.resident_model === null);
  const neededModel = typeof planErrorDetails.needed === 'string' ? planErrorDetails.needed : probes?.items.find((p) => p.probe_id === probeId)?.hf_id ?? null;
  const recheckModel = useCallback(() => {
    void fetchProbes();
    const current = latestBody.current;
    if (current?.valid) void fetchPlan(current.body);
  }, [fetchProbes, fetchPlan]);
  useEffect(() => {
    if (!waitingForModel) return undefined;
    const timer = setInterval(recheckModel, RESIDENT_POLL_MS);
    const onFocus = () => recheckModel();
    const onVisible = () => {
      if (document.visibilityState === 'visible') recheckModel();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [waitingForModel, recheckModel]);

  const keepRunning = keepShare && !['completed', 'failed'].includes(keepShare.status);
  useEffect(() => {
    if (!keepRunning) return undefined;
    const timer = setInterval(() => void pollKeepShare(), 1500);
    return () => clearInterval(timer);
  }, [keepRunning, pollKeepShare]);

  const field = 'w-full rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  // Completed versions, newest first (the API's order=recent), narrowed by the search box; the
  // journey's own version is always listed, even when older versions crowd it out of the page.
  const q = versionQuery.trim().toLowerCase();
  // By default only each dataset's newest built version, without test builds (re-walk finding 11:
  // the list held 100+ old test versions); a search, or the toggle, reaches every version.
  const built = versionList.filter((v) => v.state === 'completed');
  const hiddenVersions = built.filter((v) => !isEverydayVersion(v) && v.id !== version && v.id !== versionId).length;
  const completed = built.filter((v) =>
    q ? v.dataset_name.toLowerCase().includes(q) || v.id === version : showAllVersions || isEverydayVersion(v) || v.id === version || v.id === versionId,
  );
  const versionChoices = journeyVersion && !completed.some((v) => v.id === journeyVersion.id) ? [{ ...journeyVersion }, ...completed] : completed;
  const chosenVersion = versionList.find((v) => v.id === version) ?? (journeyVersion?.id === version ? journeyVersion : undefined);
  const probe = isProbe ? probes?.items.find((p) => p.probe_id === probeId) : undefined;
  const tags = useMemo(() => probeTags(probes?.items ?? []), [probes]);
  const rows = plan?.rows_to_score ?? 0;
  // Before a plan answers, the most the run can label: the cap, else the version's rows (finding 7).
  const expectedRows = typeof cap === 'number' ? Math.min(cap, chosenVersion?.total_rows ?? cap) : chosenVersion?.total_rows ?? null;
  const gateFailed = isProbe && plan?.reproduction?.state === 'failed';
  // Every reason the start button is disabled, in words (finding 9: it read "Label 0 rows").
  const missing = [
    ...(!version ? ['Choose the version to label.'] : []),
    ...(isProbe && !probeId ? ['Choose a probe.'] : []),
    ...(isProbe && column.trim() === '' ? ['Name the column the probe reads.'] : []),
    ...(role === 'classifier' && !templateId ? ['Choose a decision template.'] : []),
    ...(role === 'classifier' && question.trim() === '' ? ['Type the question.'] : []),
    ...(role === 'classifier' && !thresholdsValid(positive, negative) ? ['Set both thresholds.'] : []),
    ...(role === 'judge' && !rubricId ? ['Choose a rubric.'] : []),
    ...(cap === 'invalid' ? ['Fix "Rows to label".'] : []),
  ];
  const notReady = [
    ...missing,
    ...(missing.length === 0 && !plan && !planError ? ['Counting the rows…'] : []),
    ...(missing.length === 0 && planError && !(isProbe && planRefusal) ? [planError] : []),
    ...(missing.length === 0 && isProbe && planRefusal ? ['Cannot start: the reproduction check has no target yet (see above).'] : []),
    ...(gateFailed ? ['The reproduction check failed before; retry it with a reason (above) or choose another probe.'] : []),
    ...(followedLive ? ['The run above is still labeling; wait for it to finish, or cancel it on Label runs.'] : []),
    ...(starting ? ['Starting the run…'] : []),
  ];
  const estimate = keepShare?.status === 'completed' && keepShare.share !== null
    ? { share: keepShare.share, lo: keepShare.lo ?? 0, hi: keepShare.hi ?? 0, n: keepShare.n ?? 0 }
    : null;

  return (
    <div data-testid="label-step">
      <h2 className="font-semibold mb-1">Label the rows</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">{PIN_HINT}</p>
      <div className="grid gap-3 sm:grid-cols-2 mb-3">
        <div className="text-xs">
          <label htmlFor="label-version" className="block text-slate-500 dark:text-slate-400 mb-1">Version to label (newest first)</label>
          <select id="label-version" aria-label="Version to label" value={version} onChange={(e) => { setChosenByUser(true); setVersion(e.target.value); }} className={field}>
            <option value="">{versionChoices.length ? 'Choose a version' : 'No built version yet: build one on the Curate step'}</option>
            {versionChoices.map((v) => <option key={v.id} value={v.id}>{v.dataset_name} v{v.number} ({v.total_rows.toLocaleString()} rows){v.id === versionId ? ' · this dataset' : ''}</option>)}
          </select>
          {versionList.length > 8 && (
            <input aria-label="Find a version" placeholder="Find by dataset name" value={versionQuery} onChange={(e) => setVersionQuery(e.target.value)} className={`${field} mt-1`} />
          )}
          {(hiddenVersions > 0 || showAllVersions) && !q && (
            <button type="button" className="mt-1 text-indigo-600 dark:text-indigo-400 hover:underline focus-visible:ring-2 focus-visible:ring-indigo-500" aria-pressed={showAllVersions} onClick={() => setShowAllVersions((v) => !v)} data-testid="toggle-old-versions">
              {showAllVersions ? 'Hide older and test versions' : `Show ${hiddenVersions.toLocaleString()} older and test version${hiddenVersions === 1 ? '' : 's'}`}
            </button>
          )}
        </div>
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
                {(probes?.items ?? []).map((p) => <option key={p.probe_id} value={p.probe_id}>{probeOptionText(p, tags[p.probe_id])}</option>)}
              </select>
              {probe && (
                <span className="block mt-1 text-slate-500 dark:text-slate-400" data-testid="probe-facts">
                  {probeFactLines(probe).join(' ')}
                  {groupSize(probes?.items ?? [], probe) > 1 && ` ${groupSize(probes?.items ?? [], probe)} probes detect the same thing on this model and layer: the recommended one has the highest AUROC, the newest was imported last.`}
                </span>
              )}
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
      <label className="block text-xs mb-3 max-w-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Rows to label (empty: every row)</span>
        <input aria-label="Rows to label" inputMode="numeric" value={rowCap} onChange={(e) => setRowCap(e.target.value)} className={`${field} font-mono`} />
        <span className={`block mt-1 ${cap === 'invalid' ? 'text-red-600 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`} data-testid="row-cap-hint">
          {cap === 'invalid'
            ? 'Type a whole number of rows above 0, or leave it empty.'
            : typeof cap === 'number'
              ? `Labels at most ${cap.toLocaleString()} rows${chosenVersion ? ` of the version's ${chosenVersion.total_rows.toLocaleString()}` : ''}: always the same rows (the lowest row keys), and a resume stays inside them.`
              : 'Caps the run: it labels that many rows and no more.'}
        </span>
      </label>
      {role === 'judge' && showRubrics && (
        <div className="mb-4 rounded-card border border-slate-200 dark:border-slate-700 p-3">
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
      <div className="stack-y-3 mb-4">
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
      {plan?.probe && <ProbeSummary probe={plan.probe} listed={probe} reproduction={plan.reproduction ?? null} />}
      {plan?.probe && (
        <details className="mb-3 rounded-control border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="probe-details">
          <summary className="cursor-pointer text-sm text-slate-600 dark:text-slate-300 focus-visible:ring-2 focus-visible:ring-indigo-500">Details: the probe, the preflight and the reproduction check</summary>
          <div className="mt-2">
            <ProbePlanLine probe={plan.probe} />
            {plan.reproduction && <ReproductionLine reproduction={plan.reproduction} />}
          </div>
        </details>
      )}
      {gateFailed && (
        <div className="mb-3 stack-y-2" data-testid="retry-anyway">
          <label className="block text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Why run the same check again? (recorded on the run)</span>
            <textarea aria-label="Reason to retry the reproduction check" value={retryDraft} onChange={(e) => setRetryDraft(e.target.value)} rows={2} className={field} />
          </label>
          <Button variant="secondary" disabled={retryDraft.trim() === ''} onClick={() => setRetryReason(retryDraft.trim())}>Retry the check anyway</Button>
        </div>
      )}
      {isProbe && planRefusal && <ReproductionRefusalBlock refusal={planRefusal} onLinked={() => void fetchPlan(body)} />}
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400 mb-3">{error}</p>}
      {approval && <p role="status" className="text-sm mb-3">Waiting for the operator to approve this run ({approval.approval_id}).</p>}
      {started && !runId && <p role="status" className="text-sm mb-3">Run {started.id.slice(0, 12)} started; follow it on Label runs.</p>}
      {waitingForModel && (
        <div className="mb-3 rounded-control border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm" data-testid="model-wait">
          <p className="mb-1">
            {neededModel ? `Load ${neededModel} in miLLM.` : 'Load the model in miLLM.'}{' '}
            {millmUi ? (
              <a href={`${millmUi}/models`} target="_blank" rel="noreferrer" className="text-indigo-600 dark:text-indigo-400 underline focus-visible:ring-2 focus-visible:ring-indigo-500" data-testid="millm-models-link">
                Open miLLM's Models page
              </a>
            ) : (
              <span className="text-slate-600 dark:text-slate-300">Open miLLM's Models page (its address is not configured here: MILLM_UI_URL).</span>
            )}
          </p>
          <p className="text-xs text-slate-600 dark:text-slate-300">
            This step checks miLLM again every {RESIDENT_POLL_MS / 1000} seconds and when you come back to this tab; your choices stay.{' '}
            <button type="button" className="text-indigo-600 dark:text-indigo-400 hover:underline focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={recheckModel}>Check again</button>
          </p>
        </div>
      )}
      {runId && <RunProgress runId={runId} onContinue={onContinue} continueLabel="Continue to Assemble" />}
      {notReady.length > 0 && (
        <ul className="mb-2 text-xs text-slate-600 dark:text-slate-300 list-disc pl-5 stack-y-0.5" data-testid="start-blockers" aria-label="Why the run cannot start yet">
          {notReady.map((r) => (r === planError ? <li key={r} role="alert" className="text-red-600 dark:text-red-400">{r}</li> : <li key={r}>{r}</li>))}
        </ul>
      )}
      <Button disabled={notReady.length > 0} onClick={() => {
        if (startingRef.current) return;
        startingRef.current = true;
        setStarting(true);
        void startRun(body)
          .then((run) => (run ? onStarted?.(run) : undefined))
          .finally(() => {
            startingRef.current = false;
            setStarting(false);
          });
      }}>
        {!plan
          ? expectedRows !== null ? `Label ${expectedRows.toLocaleString()} rows` : 'Label rows'
          : rows === 0 && plan.rows_reused > 0 ? `Reuse ${plan.rows_reused.toLocaleString()} labels` : `Label ${rows.toLocaleString()} rows`}
      </Button>
    </div>
  );
}

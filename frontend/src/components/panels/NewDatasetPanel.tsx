// The guided New dataset flow (FR-002.48): seven steps from /datasets/meta, panels discovered from
// their owners, state in a draft saved as you go and resumable, and commit points that save the
// recipe and build a version with progress.
//
// Novice walk 2026-10-19 (findings 1, 2, 5): the rail now marks a step done only when what it makes
// exists (journey.stepStatuses); "Build version" saves the Curate step's steps as a recipe (or a new
// revision when they changed) and builds in one action; every disabled control lists why; and the
// built version is recorded on the draft, so Profile, Label, Assemble and Export read it.
import { ExternalLink, Hammer, Save } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { HelpLink } from '@/components/help/HelpLink';
import { GuidedStepHost } from '@/components/guided/GuidedStepHost';
import { buildBlockers, choicesOf, stepCountOf, stepStatuses } from '@/components/guided/journey';
import { StepRail } from '@/components/guided/StepRail';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useBuildRoom } from '@/hooks/useBuildRoom';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { draftStepsKey, useDraftsStore } from '@/stores/draftsStore';
import { useRecipesStore } from '@/stores/recipesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { InputRef } from '@/types/versions';
import { navigate } from '@/utils/navigate';

export function NewDatasetPanel({ panel }: { panel: PanelDef }) {
  const meta = useDatasetsStore((s) => s.meta);
  const fetchMeta = useDatasetsStore((s) => s.fetchMeta);
  const draft = useDraftsStore((s) => s.draft);
  const saving = useDraftsStore((s) => s.saving);
  const draftError = useDraftsStore((s) => s.error);
  const validation = useDraftsStore((s) => s.validation);
  const validatedKey = useDraftsStore((s) => s.validatedKey);
  const loadDraft = useDraftsStore((s) => s.loadDraft);
  const validateDraft = useDraftsStore((s) => s.validateDraft);
  const setStep = useDraftsStore((s) => s.setStep);
  const updateDraft = useDraftsStore((s) => s.updateDraft);
  const saveAsRevision = useDraftsStore((s) => s.saveAsRevision);
  const discardDraft = useDraftsStore((s) => s.discardDraft);
  const flushDraft = useDraftsStore((s) => s.flushDraft);
  const buildFromRecipe = useRecipesStore((s) => s.buildFromRecipe);
  const recipeError = useRecipesStore((s) => s.error);
  const build = useVersionsStore((s) => s.build);
  const trackBuild = useVersionsStore((s) => s.trackBuild);
  const fetchVersion = useVersionsStore((s) => s.fetchVersion);
  const selectVersion = useVersionsStore((s) => s.selectVersion);
  const [recipeName, setRecipeName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useBuildRoom();

  useEffect(() => {
    void fetchMeta();
    void loadDraft();
  }, [fetchMeta, loadDraft]);
  // Leaving the screen saves what the debounce has not sent yet.
  useEffect(() => () => void flushDraft(), [flushDraft]);

  const steps = meta?.guided_steps ?? [];
  const current = draft?.flow_state.step ?? steps[0] ?? 'import';
  const at = steps.indexOf(current);
  const advance = () => steps[at + 1] && setStep(steps[at + 1]);
  const stepCount = stepCountOf(draft);
  const inputs = (draft?.inputs ?? []) as InputRef[];
  const choices = choicesOf(draft);
  const name = recipeName ?? (choices.dataset_name ? `${choices.dataset_name}-recipe` : '');
  const building = Boolean(build && build.jobId === choices.build_job_id && !['completed', 'failed', 'cancelled'].includes(build.status));
  const stepsKey = draftStepsKey(draft);
  const blockers = buildBlockers(draft, { validation, validationCurrent: validatedKey === stepsKey, recipeName: name, building });
  const saveBlockers = [
    ...(stepCount === 0 ? ['Add at least one step on the Curate step.'] : []),
    ...(!draft?.recipe_id && !name.trim() ? ['Name the recipe.'] : []),
  ];

  // The steps are checked against the registry as they change, so the Curate step marks problems
  // beside each step and Build version knows whether it can run.
  useEffect(() => {
    if (stepCount === 0) return undefined;
    const timer = setTimeout(() => void validateDraft(), VALIDATE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [stepsKey, stepCount, validateDraft]);

  // The version this journey built is recorded on the draft once, so every later step reads it.
  useEffect(() => {
    if (!draft || !build || build.status !== 'completed' || !build.versionId) return;
    if (build.jobId !== choices.build_job_id || choices.version_id === build.versionId) return;
    updateDraft({ flow_state: { ...draft.flow_state, choices: { ...choices, version_id: build.versionId } } });
  }, [draft, build, choices, updateDraft]);
  // After a reload, a build this journey started is followed again until it finishes.
  useEffect(() => {
    if (choices.build_job_id && !choices.version_id && !build) trackBuild(choices.build_job_id);
  }, [choices.build_job_id, choices.version_id, build, trackBuild]);
  useEffect(() => {
    if (choices.version_id) void fetchVersion(choices.version_id);
  }, [choices.version_id, fetchVersion]);

  /** Save the steps as the recipe (a new revision only when they changed since the last save). */
  const saveSteps = async (): Promise<boolean> => {
    await flushDraft();
    const before = useDraftsStore.getState().draft;
    if (!before) return false;
    const key = draftStepsKey(before);
    if (before.recipe_id && choicesOf(before).saved_steps === key) return true;
    const revision = await saveAsRevision(before.recipe_id ? undefined : name.trim());
    const after = useDraftsStore.getState().draft;
    if (!revision || !after) return false;
    updateDraft({ flow_state: { ...after.flow_state, choices: { ...choicesOf(after), saved_steps: key } } });
    return true;
  };

  const saveAndBuild = async () => {
    setBusy(true);
    try {
      if (!(await saveSteps())) return;
      const saved = useDraftsStore.getState().draft;
      if (!saved?.recipe_id || !saved.dataset_id) return;
      const result = await buildFromRecipe(saved.recipe_id, { dataset_id: saved.dataset_id, inputs: saved.inputs as InputRef[] });
      if (!result) return;
      const latest = useDraftsStore.getState().draft ?? saved;
      if ('job_id' in result) {
        trackBuild(result.job_id);
        updateDraft({ flow_state: { ...latest.flow_state, choices: { ...choicesOf(latest), build_job_id: result.job_id, version_id: undefined } } });
      } else {
        // The same request was built before: that version is this journey's version.
        updateDraft({ flow_state: { ...latest.flow_state, choices: { ...choicesOf(latest), version_id: result.id } } });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title={choices.dataset_name ? `New dataset ${choices.dataset_name}` : panel.title}
        subtitle={`${panel.subtitle} Step ${at + 1} of ${steps.length || 7}; ${saving ? 'saving…' : 'progress is saved as you go'}.`}
        right={
          <span className="flex items-center gap-3">
            <HelpLink topic={current in HELP_FOR_STEP ? HELP_FOR_STEP[current] : 'journey'} label="Help with this step" />
            {draft && <Button variant="ghost" onClick={() => void discardDraft().then(loadDraft)}>Start over</Button>}
          </span>
        }
      />
      {(draftError || recipeError) && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{draftError ?? recipeError}</div>}
      {!draft ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="draft-loading">Loading your draft…</p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 lg:col-span-1">
            <StepRail steps={steps} current={current} onSelect={setStep} statuses={stepStatuses(steps, draft)} />
          </div>
          <div className="lg:col-span-2 min-w-0 stack-y-5">
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
              <GuidedStepHost step={current} draft={draft} onAdvance={advance} />
              {current === 'assemble' && (
                <p className="text-sm mt-4">
                  Need synthetic rows? <button type="button" className="text-indigo-700 dark:text-indigo-300 underline inline-flex items-center gap-1 rounded-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => navigate('generation')}>Open Generation<ExternalLink className="w-3 h-3" aria-hidden="true" /></button> — a held-out split is cut before any generated rows.
                </p>
              )}
            </div>
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5" data-testid="commit-points">
              <h2 className="font-semibold mb-2">Save and build</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
                The draft holds {stepCount} recipe step{stepCount === 1 ? '' : 's'} and {inputs.length} input{inputs.length === 1 ? '' : 's'}. Build version saves the steps as a recipe and builds the version in the background.
              </p>
              <div className="flex flex-wrap items-end gap-2">
                {!draft.recipe_id && (
                  <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Recipe name</span>
                    <input aria-label="Recipe name" value={name} onChange={(e) => setRecipeName(e.target.value)} className="rounded-sm border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" />
                  </label>
                )}
                <Button variant="secondary" leftIcon={<Save className="w-4 h-4" />} disabled={saveBlockers.length > 0 || busy} onClick={() => void saveSteps()}>
                  Save draft as recipe
                </Button>
                <Button leftIcon={<Hammer className="w-4 h-4" />} disabled={blockers.length > 0 || busy} loading={busy} onClick={() => void saveAndBuild()}>
                  Build version
                </Button>
              </div>
              {blockers.length > 0 && (
                <ul className="mt-3 text-xs text-slate-600 dark:text-slate-300 list-disc pl-5 stack-y-0.5" data-testid="build-blockers" aria-label="Why Build version is not available yet">
                  {blockers.map((b) => <li key={b}>{b}</li>)}
                </ul>
              )}
              {build && build.jobId === choices.build_job_id && (
                <div className="mt-3 text-sm" role="status" data-testid="build-progress">
                  {build.status === 'completed' && build.versionId ? (
                    <span>
                      Built. <button type="button" className="text-indigo-700 dark:text-indigo-300 underline rounded-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => { selectVersion(build.versionId); navigate('version'); }}>Open the version</button>
; Label reads it.
                    </span>
                  ) : build.status === 'failed' ? (
                    <span className="text-red-700 dark:text-red-300">The build failed: {build.error}</span>
                  ) : (
                    <span>Building: {build.phase ?? 'queued'}, {Math.round(build.percent)}%</span>
                  )}
                </div>
              )}
              {choices.version_id && !(build && build.jobId === choices.build_job_id) && (
                <p className="mt-3 text-sm" role="status" data-testid="journey-version">This dataset's version is built; Label reads it.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export const VALIDATE_DEBOUNCE_MS = 500;

/** The help topic for each guided step (config/help.ts). */
const HELP_FOR_STEP: Record<string, string> = {
  import: 'import',
  goal: 'goal',
  profile: 'profile',
  curate: 'curate',
  label: 'label',
  assemble: 'assemble',
  export: 'export',
};

// The guided New dataset flow (FR-002.48): seven steps from /datasets/meta, panels discovered from
// their owners, state in a draft saved as you go and resumable, and commit points that save the
// recipe and build a version with progress.
import { ExternalLink, Hammer, Save } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { GuidedStepHost } from '@/components/guided/GuidedStepHost';
import { StepRail } from '@/components/guided/StepRail';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useBuildRoom } from '@/hooks/useBuildRoom';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useDraftsStore } from '@/stores/draftsStore';
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
  const loadDraft = useDraftsStore((s) => s.loadDraft);
  const setStep = useDraftsStore((s) => s.setStep);
  const saveAsRevision = useDraftsStore((s) => s.saveAsRevision);
  const discardDraft = useDraftsStore((s) => s.discardDraft);
  const flushDraft = useDraftsStore((s) => s.flushDraft);
  const buildFromRecipe = useRecipesStore((s) => s.buildFromRecipe);
  const recipeError = useRecipesStore((s) => s.error);
  const build = useVersionsStore((s) => s.build);
  const trackBuild = useVersionsStore((s) => s.trackBuild);
  const selectVersion = useVersionsStore((s) => s.selectVersion);
  const [recipeName, setRecipeName] = useState('');
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
  const stepCount = (draft?.body as { steps?: unknown[] } | undefined)?.steps?.length ?? 0;
  const inputs = (draft?.inputs ?? []) as InputRef[];
  const choices = (draft?.flow_state.choices ?? {}) as { dataset_name?: string };

  const startBuild = async () => {
    if (!draft?.recipe_id || !draft.dataset_id) return;
    const result = await buildFromRecipe(draft.recipe_id, { dataset_id: draft.dataset_id, inputs });
    if (!result) return;
    if ('job_id' in result) trackBuild(result.job_id);
    else {
      selectVersion(result.id);
      navigate('version');
    }
  };

  return (
    <>
      <PageHead
        title={choices.dataset_name ? `New dataset ${choices.dataset_name}` : panel.title}
        subtitle={`${panel.subtitle} Step ${at + 1} of ${steps.length || 7}; ${saving ? 'saving…' : 'progress is saved as you go'}.`}
        right={draft && <Button variant="ghost" onClick={() => void discardDraft().then(loadDraft)}>Start over</Button>}
      />
      {(draftError || recipeError) && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{draftError ?? recipeError}</div>}
      {!draft ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="draft-loading">Loading your draft…</p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 lg:col-span-1">
            <StepRail steps={steps} current={current} onSelect={setStep} />
          </div>
          <div className="lg:col-span-2 min-w-0 space-y-5">
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
              <GuidedStepHost step={current} draft={draft} onAdvance={advance} />
              {current === 'assemble' && (
                <p className="text-sm mt-4">
                  Need synthetic rows? <button type="button" className="text-indigo-700 dark:text-indigo-300 underline inline-flex items-center gap-1 rounded focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => navigate('generation')}>Open Generation<ExternalLink className="w-3 h-3" aria-hidden="true" /></button> — a held-out split is cut before any generated rows.
                </p>
              )}
            </div>
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5" data-testid="commit-points">
              <h2 className="font-semibold mb-2">Save and build</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
                The draft holds {stepCount} recipe step{stepCount === 1 ? '' : 's'} and {inputs.length} input{inputs.length === 1 ? '' : 's'}. Saving validates every step; building runs in the background.
              </p>
              <div className="flex flex-wrap items-end gap-2">
                {!draft.recipe_id && (
                  <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Recipe name</span>
                    <input value={recipeName} onChange={(e) => setRecipeName(e.target.value)} className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" />
                  </label>
                )}
                <Button variant="secondary" leftIcon={<Save className="w-4 h-4" />} disabled={stepCount === 0 || (!draft.recipe_id && !recipeName.trim())} onClick={() => void saveAsRevision(recipeName.trim() || undefined)}>
                  Save draft as recipe
                </Button>
                <Button leftIcon={<Hammer className="w-4 h-4" />} disabled={!draft.recipe_id || !draft.dataset_id || inputs.length === 0} onClick={() => void startBuild()}>
                  Build version
                </Button>
              </div>
              {build && (
                <div className="mt-3 text-sm" role="status" data-testid="build-progress">
                  {build.status === 'completed' && build.versionId ? (
                    <button type="button" className="text-indigo-700 dark:text-indigo-300 underline rounded focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => { selectVersion(build.versionId); navigate('version'); }}>Built. Open the version.</button>
                  ) : build.status === 'failed' ? (
                    <span className="text-red-700 dark:text-red-300">The build failed: {build.error}</span>
                  ) : (
                    <span>Building: {build.phase ?? 'queued'}, {Math.round(build.percent)}%</span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

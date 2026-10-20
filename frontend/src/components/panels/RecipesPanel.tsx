// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/panels/TrainingTemplatesPanel.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: list plus create and edit, import and export, notifications.
// Changed: recipes are revised, cloned and archived (never edited in place or deleted); export
// downloads the backend's bytes; import reports created, already present or refused with reasons;
// "Build version" starts a build from the head revision.
import { Plus, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/common/Button';
import { PageHead } from '@/components/layout/PageHead';
import { RecipeEditor } from '@/components/recipes/RecipeEditor';
import { RecipeList } from '@/components/recipes/RecipeList';
import { InputPicker } from '@/components/versions/InputPicker';
import type { PanelDef } from '@/config/panels';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useRecipesStore } from '@/stores/recipesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { Recipe, RecipeSummary } from '@/types/recipes';
import type { InputRef } from '@/types/versions';
import { navigate } from '@/utils/navigate';

type Mode = { kind: 'list' } | { kind: 'new' } | { kind: 'edit'; recipe: Recipe } | { kind: 'clone'; recipe: RecipeSummary } | { kind: 'build'; recipe: RecipeSummary };

export function RecipesPanel({ panel }: { panel: PanelDef }) {
  const recipes = useRecipesStore((s) => s.recipes);
  const showArchived = useRecipesStore((s) => s.showArchived);
  const validation = useRecipesStore((s) => s.validation);
  const importResult = useRecipesStore((s) => s.importResult);
  const error = useRecipesStore((s) => s.error);
  const notice = useRecipesStore((s) => s.notice);
  const fetchRecipes = useRecipesStore((s) => s.fetchRecipes);
  const setShowArchived = useRecipesStore((s) => s.setShowArchived);
  const fetchRecipe = useRecipesStore((s) => s.fetchRecipe);
  const validateBody = useRecipesStore((s) => s.validateBody);
  const createRecipe = useRecipesStore((s) => s.createRecipe);
  const reviseRecipe = useRecipesStore((s) => s.reviseRecipe);
  const cloneRecipe = useRecipesStore((s) => s.cloneRecipe);
  const archiveRecipe = useRecipesStore((s) => s.archiveRecipe);
  const exportRecipe = useRecipesStore((s) => s.exportRecipe);
  const importRecipe = useRecipesStore((s) => s.importRecipe);
  const buildFromRecipe = useRecipesStore((s) => s.buildFromRecipe);
  const datasets = useDatasetsStore((s) => s.datasets);
  const fetchDatasets = useDatasetsStore((s) => s.fetchDatasets);
  const trackBuild = useVersionsStore((s) => s.trackBuild);
  const selectVersion = useVersionsStore((s) => s.selectVersion);
  const [mode, setMode] = useState<Mode>({ kind: 'list' });
  const [name, setName] = useState('');
  const [build, setBuild] = useState<{ dataset: string; input: InputRef | null; seed: string }>({ dataset: '', input: null, seed: '' });
  const [buildNotice, setBuildNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void fetchRecipes();
    void fetchDatasets();
  }, [fetchRecipes, fetchDatasets]);

  const actions = {
    onEdit: async (r: RecipeSummary) => {
      const full = await fetchRecipe(r.id);
      if (full) setMode({ kind: 'edit', recipe: full });
    },
    onClone: (r: RecipeSummary) => { setName(`${r.name}-copy`); setMode({ kind: 'clone', recipe: r }); },
    onExport: (r: RecipeSummary) => r.head_revision_id && void exportRecipe(r.id, r.head_revision_id, r.name),
    onArchive: (r: RecipeSummary) => void archiveRecipe(r.id),
    onBuild: (r: RecipeSummary) => setMode({ kind: 'build', recipe: r }),
  };

  const startBuild = async (recipe: RecipeSummary) => {
    if (!build.input) return;
    const result = await buildFromRecipe(recipe.id, { dataset_id: build.dataset, inputs: [build.input], seed: build.seed ? Number(build.seed) : null });
    if (!result) return;
    if ('job_id' in result) {
      trackBuild(result.job_id);
      setBuildNotice(`Build started (job ${result.job_id}, seed ${result.seed}). Its progress is in Operations.`);
    } else {
      selectVersion(result.id);
      navigate('version');
    }
    setMode({ kind: 'list' });
  };

  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  return (
    <>
      <PageHead help="recipes"
        title={panel.title}
        subtitle={panel.subtitle}
        right={
          <div className="flex gap-2">
            <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" aria-label="Recipe file to import"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void importRecipe(f); e.target.value = ''; }} />
            <Button variant="secondary" leftIcon={<Upload className="w-4 h-4" />} onClick={() => fileRef.current?.click()}>Import recipe</Button>
            <Button leftIcon={<Plus className="w-4 h-4" />} onClick={() => setMode({ kind: 'new' })}>New recipe</Button>
          </div>
        }
      />
      {(notice || buildNotice) && <div role="status" className="mb-4 rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50 dark:bg-indigo-500/10 px-4 py-2 text-sm">{buildNotice ?? notice}</div>}
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      {importResult && (
        <div role="status" className="mb-4 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-2 text-sm" data-testid="import-result">
          <span className="font-medium">
            {importResult.outcome === 'created' ? 'Imported.' : importResult.outcome === 'already_present' ? 'Already present: this recipe and hash exist.' : 'Refused.'}
          </span>
          {importResult.hash && <span className="font-mono text-xs ml-2">{importResult.hash.slice(0, 12)}…</span>}
          {importResult.reasons.map((r, i) => <div key={i} className="text-xs text-red-700 dark:text-red-300">{r.message}</div>)}
          {importResult.notes.map((n, i) => <div key={i} className="text-xs text-slate-500">{n}</div>)}
        </div>
      )}
      {(mode.kind === 'new' || mode.kind === 'edit') && (
        <RecipeEditor
          key={mode.kind === 'edit' ? mode.recipe.id : 'new'}
          recipe={mode.kind === 'edit' ? mode.recipe : null}
          validation={validation}
          onValidate={(b) => void validateBody(b)}
          onCancel={() => setMode({ kind: 'list' })}
          onSave={async (n, b, labels, d) => {
            const ok = mode.kind === 'edit' ? await reviseRecipe(mode.recipe.id, b, labels) : Boolean(await createRecipe(n, b, labels, d || undefined));
            if (ok) setMode({ kind: 'list' });
          }}
        />
      )}
      {mode.kind === 'clone' && (
        <div className="mb-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 flex flex-wrap items-end gap-2">
          <label className="flex-1 text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Name for the clone of {mode.recipe.name}</span><input value={name} onChange={(e) => setName(e.target.value)} className={field} /></label>
          <Button onClick={async () => { if (await cloneRecipe(mode.recipe.id, name.trim())) setMode({ kind: 'list' }); }} disabled={!name.trim()}>Clone recipe</Button>
          <Button variant="ghost" onClick={() => setMode({ kind: 'list' })}>Cancel</Button>
        </div>
      )}
      {mode.kind === 'build' && (
        <div className="mb-6 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
          <h2 className="font-semibold mb-3">Build a version from {mode.recipe.name}</h2>
          <div className="grid gap-3 sm:grid-cols-4">
            <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Dataset</span>
              <select value={build.dataset} onChange={(e) => setBuild({ ...build, dataset: e.target.value })} className={field}>
                <option value="">Choose a dataset</option>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
            <div className="sm:col-span-2"><InputPicker value={build.input} onChange={(input) => setBuild({ ...build, input })} /></div>
            <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Seed (random if empty)</span><input value={build.seed} inputMode="numeric" onChange={(e) => setBuild({ ...build, seed: e.target.value.replace(/\D/g, '') })} className={`${field} font-mono`} /></label>
          </div>
          <div className="flex gap-2 justify-end mt-3">
            <Button variant="ghost" onClick={() => setMode({ kind: 'list' })}>Cancel</Button>
            <Button onClick={() => void startBuild(mode.recipe)} disabled={!build.dataset || !build.input}>Build version</Button>
          </div>
          {(!build.dataset || !build.input) && (
            <p className="mt-2 text-xs text-right text-slate-500 dark:text-slate-400" data-testid="build-blocked">
              {!build.dataset ? 'Choose the dataset the version belongs to' : 'Choose what to build from'} to build.
            </p>
          )}
        </div>
      )}
      <label className="flex items-center gap-2 text-sm mb-3">
        <input type="checkbox" checked={showArchived} onChange={(e) => void setShowArchived(e.target.checked)} className="focus-visible:ring-2 focus-visible:ring-indigo-500" />
        Show archived recipes
      </label>
      <RecipeList recipes={recipes} actions={actions} />
    </>
  );
}

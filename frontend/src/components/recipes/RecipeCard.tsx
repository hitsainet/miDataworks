// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/trainingTemplates/TrainingTemplateCard.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: a card per item with its actions on the right, stopping click
// propagation. Changed: name, short hash, step count, providers, revisions and versions built
// (FR-002.41); actions name what they do; delete replaced by archive; favourites dropped.
import { Archive, Download, FileCode2, GitBranch, Hammer, Pencil } from 'lucide-react';

import { IdentifierChip } from '@/components/versions/IdentifierChip';
import type { RecipeSummary } from '@/types/recipes';

export interface RecipeActions {
  onEdit: (r: RecipeSummary) => void;
  onClone: (r: RecipeSummary) => void;
  onExport: (r: RecipeSummary) => void;
  onArchive: (r: RecipeSummary) => void;
  onBuild: (r: RecipeSummary) => void;
}

const action = 'inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-40';

export function RecipeCard({ recipe, actions }: { recipe: RecipeSummary; actions: RecipeActions }) {
  return (
    <article className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4" data-testid="recipe-card">
      <div className="flex items-start gap-3">
        <FileCode2 className="w-5 h-5 mt-0.5 text-slate-500 dark:text-slate-400 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold truncate">{recipe.name}</h3>
            {recipe.archived && <span className="text-xs text-amber-700 dark:text-amber-400">archived</span>}
          </div>
          {recipe.description && <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{recipe.description}</p>}
          <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs text-slate-500 dark:text-slate-400">
            <IdentifierChip value={recipe.head_hash} label="hash" />
            <span>{recipe.step_count} steps</span>
            <span>{recipe.providers.join(', ') || 'no providers'}</span>
            <span>{recipe.revision_count} revisions</span>
            <span>{recipe.versions_built} versions built</span>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-1 mt-3">
        <button type="button" className={action} onClick={() => actions.onEdit(recipe)} disabled={recipe.archived}><Pencil className="w-3.5 h-3.5" aria-hidden="true" />Edit recipe</button>
        <button type="button" className={action} onClick={() => actions.onClone(recipe)}><GitBranch className="w-3.5 h-3.5" aria-hidden="true" />Clone recipe</button>
        <button type="button" className={action} onClick={() => actions.onExport(recipe)} disabled={!recipe.head_revision_id}><Download className="w-3.5 h-3.5" aria-hidden="true" />Export recipe</button>
        <button type="button" className={action} onClick={() => actions.onBuild(recipe)} disabled={recipe.archived}><Hammer className="w-3.5 h-3.5" aria-hidden="true" />Build version</button>
        {!recipe.archived && <button type="button" className={action} onClick={() => actions.onArchive(recipe)}><Archive className="w-3.5 h-3.5" aria-hidden="true" />Archive recipe</button>}
      </div>
    </article>
  );
}

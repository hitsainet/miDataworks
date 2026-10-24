// A searchable list of recipe cards (FR-002.41).
import { useState } from 'react';

import type { RecipeSummary } from '@/types/recipes';

import { RecipeCard } from './RecipeCard';
import type { RecipeActions } from './RecipeCard';

export function RecipeList({ recipes, actions }: { recipes: RecipeSummary[]; actions: RecipeActions }) {
  const [query, setQuery] = useState('');
  const shown = recipes.filter((r) => r.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <div>
      <label className="block mb-3"><span className="sr-only">Search recipes</span>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search recipes" className="w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" />
      </label>
      {shown.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">{recipes.length === 0 ? 'No recipes yet. Create one, or import a recipe file.' : 'No recipe matches the search.'}</p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">{shown.map((r) => <RecipeCard key={r.id} recipe={r} actions={actions} />)}</div>
      )}
    </div>
  );
}

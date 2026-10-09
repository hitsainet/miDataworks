// The recipe editor (FR-002.41): add, remove and reorder steps (keyboard buttons, not drag only),
// validate against feature 003's registry, and show each step's errors beside it.
import { Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import type { Recipe, RecipeBody, StepError, Validation } from '@/types/recipes';

import { StepRow } from './StepRow';
import type { EditableStep } from './StepRow';

export function toEditable(recipe: Recipe | null): EditableStep[] {
  const head = recipe?.revisions.find((r) => r.id === recipe.head_revision_id);
  if (!head) return [{ operator: '', version: '', params: '{}', label: '' }];
  return head.body.steps.map((s, i) => ({ operator: s.operator, version: s.version, params: JSON.stringify(s.params), label: head.step_labels[i] ?? '' }));
}

export function toBody(steps: EditableStep[]): { body: RecipeBody; labels: string[]; parseErrors: Record<number, string> } {
  const parseErrors: Record<number, string> = {};
  const body: RecipeBody = {
    format: 'dw.recipe/v1',
    steps: steps.map((s, i) => {
      let params: Record<string, unknown> = {};
      try {
        params = s.params.trim() ? (JSON.parse(s.params) as Record<string, unknown>) : {};
      } catch {
        parseErrors[i] = 'The parameters are not valid JSON.';
      }
      return { operator: s.operator.trim(), version: s.version.trim(), params };
    }),
  };
  const labels = steps.some((s) => s.label.trim()) ? steps.map((s) => s.label.trim()) : [];
  return { body, labels, parseErrors };
}

export function RecipeEditor({
  recipe,
  validation,
  onValidate,
  onSave,
  onCancel,
}: {
  recipe: Recipe | null;
  validation: Validation | null;
  onValidate: (body: RecipeBody) => void;
  onSave: (name: string, body: RecipeBody, labels: string[], description: string) => void;
  onCancel: () => void;
}) {
  const [steps, setSteps] = useState<EditableStep[]>(() => toEditable(recipe));
  const [name, setName] = useState(recipe?.name ?? '');
  const [description, setDescription] = useState(recipe?.description ?? '');
  const { body, labels, parseErrors } = toBody(steps);
  const errorsFor = (i: number): StepError[] => [
    ...(parseErrors[i] ? [{ code: 'params_json', message: parseErrors[i] }] : []),
    ...(validation?.steps.find((s) => s.index === i + 1)?.errors ?? []),
  ];
  const move = (i: number, delta: -1 | 1) => {
    const next = [...steps];
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    setSteps(next);
  };
  return (
    <section className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 mb-6" aria-labelledby="editor-title">
      <h2 id="editor-title" className="font-semibold mb-3">{recipe ? `Edit recipe ${recipe.name}` : 'New recipe'}</h2>
      {!recipe && (
        <label className="block text-xs mb-3"><span className="block text-slate-500 dark:text-slate-400 mb-1">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" />
        </label>
      )}
      {!recipe && (
        <label className="block text-xs mb-3"><span className="block text-slate-500 dark:text-slate-400 mb-1">Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" />
        </label>
      )}
      {validation && validation.body_errors.length > 0 && (
        <ul role="alert" className="mb-3 text-xs text-red-700 dark:text-red-300">{validation.body_errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul>
      )}
      <ol className="space-y-3 mb-3">
        {steps.map((s, i) => (
          <StepRow key={i} step={s} index={i} count={steps.length} errors={errorsFor(i)}
            onChange={(next) => setSteps(steps.map((x, j) => (j === i ? next : x)))}
            onMove={(delta) => move(i, delta)}
            onRemove={() => setSteps(steps.filter((_, j) => j !== i))} />
        ))}
      </ol>
      <div className="flex flex-wrap gap-2 justify-between">
        <Button variant="secondary" leftIcon={<Plus className="w-4 h-4" />} onClick={() => setSteps([...steps, { operator: '', version: '', params: '{}', label: '' }])}>Add step</Button>
        <span className="flex gap-2">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="secondary" onClick={() => onValidate(body)} disabled={Object.keys(parseErrors).length > 0}>Check recipe</Button>
          <Button onClick={() => onSave(name.trim(), body, labels, description.trim())} disabled={Object.keys(parseErrors).length > 0 || (!recipe && !name.trim())}>
            {recipe ? 'Save revision' : 'Save recipe'}
          </Button>
        </span>
      </div>
    </section>
  );
}

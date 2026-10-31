// The guided flow's Curate step (FR-004.44; novice walk 2026-10-19, finding 1). It used to list the
// recommended operators as plain text with no way to add one, so the draft held no steps and the
// version could never be built. Now each suggestion has an Add button, any registered operator can
// be added, every step is configured through feature 003's schema form (StepRow, shared with the
// Recipes editor), steps are checked against the registry as you go, and the build below is enabled
// as soon as the steps are valid (NewDatasetPanel runs the check; this step shows its answer).
import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/common/Button';
import { choicesOf } from '@/components/guided/journey';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useOperatorChoices } from '@/components/recipes/operatorChoices';
import { toBody } from '@/components/recipes/RecipeEditor';
import { StepRow } from '@/components/recipes/StepRow';
import type { EditableStep } from '@/components/recipes/StepRow';
import { draftStepsKey, useDraftsStore } from '@/stores/draftsStore';
import type { RecipeDraft, StepError } from '@/types/recipes';
import { navigate } from '@/utils/navigate';

/** Suggested steps, in the order they usually run: clean first, then size the data. */
export const RECOMMENDED: Array<{ name: string; title: string; why: string }> = [
  { name: 'dedup_exact', title: 'Remove exact duplicates', why: 'identical rows, kept once' },
  { name: 'empty_content', title: 'Drop empty rows', why: 'rows with nothing to read' },
  { name: 'length_band', title: 'Drop rows too short or too long', why: 'by a length band you set' },
  { name: 'dedup_minhash', title: 'Remove near duplicates', why: 'with the similarity threshold drawn' },
  { name: 'normaliser', title: 'Normalise chats', why: 'one chat format, nothing stripped' },
  { name: 'chat_json_parser', title: 'Parse chats stored as JSON text', why: 'into messages' },
  { name: 'decontaminate', title: 'Remove benchmark overlap', why: 'rows that overlap a pinned benchmark' },
  { name: 'sample', title: 'Sample rows', why: 'a seeded random sample of a fixed size, for example 25,000 rows' },
];

export function editableFrom(draft: RecipeDraft): EditableStep[] {
  return (draft.body.steps ?? []).map((s, i) => ({ operator: s.operator, version: s.version, params: JSON.stringify(s.params ?? {}), label: draft.step_labels[i] ?? '' }));
}

function CurateStep({ draft, onAdvance }: GuidedStepProps) {
  const updateDraft = useDraftsStore((s) => s.updateDraft);
  const validation = useDraftsStore((s) => s.validation);
  const validatedKey = useDraftsStore((s) => s.validatedKey);
  const catalogue = useOperatorChoices();
  const [steps, setSteps] = useState<EditableStep[]>(() => editableFrom(draft));
  const key = draftStepsKey(draft);
  const built = Boolean(choicesOf(draft).version_id);

  const { parseErrors } = useMemo(() => toBody(steps), [steps]);
  const change = (next: EditableStep[]) => {
    setSteps(next);
    const { body, labels, parseErrors: broken } = toBody(next);
    // The draft keeps what parses; a step whose JSON is broken stays on screen with its error.
    if (Object.keys(broken).length === 0) updateDraft({ body, step_labels: labels });
  };
  const add = (name: string) => {
    const choice = catalogue.status === 'ready' ? catalogue.choices.find((c) => c.name === name) : undefined;
    change([...steps, { operator: name, version: choice?.current?.version ?? '', params: '{}', label: '' }]);
  };
  const current = validatedKey === key ? validation : null;
  const errorsFor = (i: number): StepError[] => [
    ...(parseErrors[i] ? [{ code: 'params_json', message: parseErrors[i] }] : []),
    ...(current?.steps.find((s) => s.index === i + 1)?.errors ?? []),
  ];
  const choiceOf = (name: string) => (catalogue.status === 'ready' ? catalogue.choices.find((c) => c.name === name) : undefined);
  const available = (name: string) => choiceOf(name)?.current ?? null;
  const move = (i: number, delta: -1 | 1) => {
    const next = [...steps];
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    change(next);
  };

  return (
    <div data-testid="guided-curate">
      <h2 className="font-semibold mb-1">Curate</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
        Choose the steps that clean and size your data, in the order they run. Each step logs every row it removes and why. Then build the version with <span className="font-medium">Save and build</span> below.
      </p>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">Suggested steps</h3>
      <ul className="divide-y divide-slate-200 dark:divide-slate-700 mb-4 rounded-card border border-slate-200 dark:border-slate-700" data-testid="curate-suggestions">
        {RECOMMENDED.map((r) => {
          const entry = available(r.name);
          return (
            <li key={r.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{r.title}</span> <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{r.name}</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">{r.why}</span>
              </span>
              <span className="shrink-0 text-right">
                <Button size="sm" variant="secondary" leftIcon={<Plus className="w-3.5 h-3.5" />} aria-label={`Add ${r.title}`} disabled={!entry} onClick={() => add(r.name)}>Add</Button>
                {!entry && catalogue.status === 'ready' && (
                  <span className="block text-xs text-amber-700 dark:text-amber-400">{choiceOf(r.name) ? 'not allowed: allow it on Operators' : 'not installed on this deployment'}</span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {catalogue.status === 'loading' && <p role="status" className="text-xs text-slate-500 dark:text-slate-400 mb-3">Reading the operators…</p>}
      {catalogue.status === 'error' && <p role="alert" className="text-sm text-red-700 dark:text-red-400 mb-3">The operators could not be read: {catalogue.message}</p>}
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">Your recipe ({steps.length} step{steps.length === 1 ? '' : 's'})</h3>
      {steps.length === 0 ? (
        <p className="text-sm text-slate-600 dark:text-slate-300 mb-3" data-testid="curate-empty">No steps yet. Add one of the suggestions above, or any operator below. To label a fixed number of rows, add "Sample rows".</p>
      ) : (
        <ol className="stack-y-3 mb-3" data-testid="curate-steps">
          {steps.map((s, i) => (
            <StepRow key={i} step={s} index={i} count={steps.length} errors={errorsFor(i)}
              onChange={(next) => change(steps.map((x, j) => (j === i ? next : x)))}
              onMove={(delta) => move(i, delta)}
              onRemove={() => change(steps.filter((_, j) => j !== i))} />
          ))}
        </ol>
      )}
      {steps.length > 0 && (
        <p role="status" className={`text-xs mb-3 ${current && !current.valid ? 'text-red-700 dark:text-red-300' : 'text-slate-500 dark:text-slate-400'}`} data-testid="curate-check">
          {Object.keys(parseErrors).length > 0
            ? 'A step\'s settings are not valid JSON; fix it to check the recipe.'
            : !current
              ? 'Checking the steps against the operator registry…'
              : current.valid
                ? `${steps.length === 1 ? 'The step is' : `All ${steps.length} steps are`} valid. Build the version below.`
                : 'Some steps have problems; each is marked beside its step.'}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 mt-2">
        <Button variant="secondary" leftIcon={<Plus className="w-4 h-4" />} onClick={() => change([...steps, { operator: '', version: '', params: '{}', label: '' }])}>Add another step</Button>
        <span className="flex flex-wrap items-center gap-2">
          <button type="button" className="text-xs text-indigo-700 dark:text-indigo-300 underline rounded-control focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => navigate('operators')}>Preview operators on the Operators screen</button>
          <Button onClick={onAdvance} disabled={!built}>Continue to labeling</Button>
        </span>
      </div>
      {!built && <p className="text-xs text-right text-slate-500 dark:text-slate-400 mt-1" data-testid="curate-continue-why">Build the version first (Save and build, below): labeling reads a built version.</p>}
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'curate', order: 3, Component: CurateStep };
export default panel;

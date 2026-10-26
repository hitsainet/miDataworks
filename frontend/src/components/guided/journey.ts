// What the guided flow has actually done, read from the draft (novice walk 2026-10-19, finding 1:
// the rail marked a step "done" because the user had moved past it, and the Save and build buttons
// were disabled with no reason). Every rule here reads a recorded fact: a source in the inputs, a
// dataset id, a built version, a started label run.
import type { RecipeDraft, Validation } from '@/types/recipes';

export interface JourneyChoices {
  source_id?: string;
  dataset_name?: string;
  suggested_name?: string;
  target_type?: string;
  /** The version this journey built (set when its build completes). */
  version_id?: string;
  /** The build job this journey started, so its completion is recognised after a reload. */
  build_job_id?: string;
  /** draftStepsKey of the steps last saved as a recipe revision. */
  saved_steps?: string;
  /** The label run started from the Label step. */
  label_run_id?: string;
}

export const choicesOf = (draft: RecipeDraft | null): JourneyChoices => (draft?.flow_state.choices ?? {}) as JourneyChoices;

export const stepCountOf = (draft: RecipeDraft | null): number => draft?.body?.steps?.length ?? 0;

export interface StepStatus {
  done: boolean;
  /** Why the step is not done, in words; shown on the rail. */
  note: string;
}

/** Each step's real state. Steps a later release adds read as not done with no claim made. */
export function stepStatuses(steps: string[], draft: RecipeDraft | null): Record<string, StepStatus> {
  const c = choicesOf(draft);
  const built = Boolean(c.version_id);
  const rules: Record<string, StepStatus> = {
    import: { done: (draft?.inputs.length ?? 0) > 0, note: 'no source chosen' },
    goal: { done: Boolean(draft?.dataset_id), note: 'no dataset yet' },
    profile: { done: built, note: 'needs a built version' },
    curate: { done: built, note: 'no version built yet' },
    label: { done: Boolean(c.label_run_id), note: 'no label run started' },
    assemble: { done: false, note: 'checks the labeled version' },
    export: { done: false, note: 'publish or export when ready' },
  };
  return Object.fromEntries(steps.map((s) => [s, rules[s] ?? { done: false, note: '' }]));
}

/** Every reason the version cannot be built now, in the order the user would fix them. */
export function buildBlockers(
  draft: RecipeDraft | null,
  opts: { validation: Validation | null; validationCurrent: boolean; recipeName: string; building: boolean },
): string[] {
  if (!draft) return ['The draft is still loading.'];
  const out: string[] = [];
  if (draft.inputs.length === 0) out.push('Choose a source on the Import step.');
  if (!draft.dataset_id) out.push('Create the dataset on the Goal step.');
  if (stepCountOf(draft) === 0) out.push('Add at least one step on the Curate step, for example "Sample rows".');
  else if (!opts.validationCurrent) out.push('Checking the steps…');
  else if (opts.validation && !opts.validation.valid) out.push('Fix the steps marked on the Curate step.');
  if (!draft.recipe_id && !opts.recipeName.trim()) out.push('Name the recipe.');
  if (opts.building) out.push('A build is running; wait for it to finish.');
  return out;
}

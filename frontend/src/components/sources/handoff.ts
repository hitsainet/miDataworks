// Hand a ready source and its detection to feature 002's guided draft (FR-001.33, FR-001.37). One
// function, so the Datasets screen's "Build a version from this source" and the guided Import step
// send the same payload through 002's draft action.
//
// Novice walk 2026-10-19, finding 2: the source screen's button used to return the user to whatever
// step the draft was on (Curate, then a dead end). The patch now names where the journey continues:
// Goal when the draft has no dataset yet (a build needs one), else Curate, which builds. A dataset
// name is suggested from the source's own name; the user can change it on the Goal step.
import type { RecipeDraft } from '@/types/recipes';
import type { Detection } from '@/types/sources';

/** A dataset name the Goal step accepts (lower case, digits and hyphens), from a source's name. */
export function suggestedDatasetName(name: string | null | undefined): string {
  const tail = (name ?? '').split('/').pop() ?? '';
  return tail
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/g, '');
}

export function draftPatchFor(
  draft: RecipeDraft,
  source: { id: string; detection?: Detection | null; display_name?: string | null; repo_id?: string | null },
  options: { continueTo?: boolean } = {},
) {
  const choices = draft.flow_state.choices ?? {};
  const suggested = suggestedDatasetName(source.repo_id ?? source.display_name);
  const flow_state = {
    ...draft.flow_state,
    ...(options.continueTo ? { step: draft.dataset_id ? 'curate' : 'goal' } : {}),
    choices: { ...choices, source_id: source.id, detection: source.detection ?? null, ...(suggested ? { suggested_name: suggested } : {}) },
  };
  return { inputs: [{ kind: 'source', source_id: source.id }], flow_state };
}

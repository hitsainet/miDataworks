// Hand a ready source and its detection to feature 002's guided draft (FR-001.33, FR-001.37). One
// function, so the Datasets screen's "Build a version from this source" and the guided Import step
// send the same payload through 002's draft action.
import type { RecipeDraft } from '@/types/recipes';
import type { Detection } from '@/types/sources';

export function draftPatchFor(draft: RecipeDraft, source: { id: string; detection?: Detection | null }) {
  const choices = draft.flow_state.choices ?? {};
  return {
    inputs: [{ kind: 'source', source_id: source.id }],
    flow_state: { ...draft.flow_state, choices: { ...choices, source_id: source.id, detection: source.detection ?? null } },
  };
}

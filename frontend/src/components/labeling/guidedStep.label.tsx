// The guided flow's step 5, "label" (P-23: feature 002 owns the flow and discovers this file). The
// version this journey built is chosen for the user, the labeler choices are kept on the draft so a
// reload keeps them (novice re-walk 2026-10-19, finding 1), and a started run is recorded on the
// draft and followed HERE until the user continues (finding 4: Start used to jump to Assemble
// with no progress).
import { useCallback } from 'react';

import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useDraftsStore } from '@/stores/draftsStore';

import { LabelStep } from './LabelStep';
import type { LabelChoices } from './LabelStep';

type Choices = { version_id?: string; label_run_id?: string; label?: LabelChoices };

/** Merge into the CURRENT draft's choices: the prop may be a render behind a debounced save. */
function patchChoices(patch: Partial<Choices>): void {
  const { draft, updateDraft } = useDraftsStore.getState();
  if (!draft) return;
  const choices = (draft.flow_state.choices ?? {}) as Choices;
  updateDraft({ flow_state: { ...draft.flow_state, choices: { ...choices, ...patch } } });
}

function LabelGuidedStep({ draft, onAdvance }: GuidedStepProps) {
  const choices = (draft.flow_state.choices ?? {}) as Choices;
  const keepChoices = useCallback((label: LabelChoices) => patchChoices({ label }), []);
  return (
    <LabelStep
      versionId={choices.version_id ?? null}
      initial={choices.label}
      onChoicesChange={keepChoices}
      runId={choices.label_run_id ?? null}
      onStarted={(run) => patchChoices({ label_run_id: run.id })}
      onContinue={onAdvance}
    />
  );
}

const panel: GuidedStepPanel = { step: 'label', order: 5, Component: LabelGuidedStep };
export default panel;

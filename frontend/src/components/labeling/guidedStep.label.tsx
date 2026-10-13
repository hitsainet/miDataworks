// The guided flow's step 5, "label" (P-23: feature 002 owns the flow and discovers this file).
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';

import { LabelStep } from './LabelStep';

function LabelGuidedStep({ draft, onAdvance }: GuidedStepProps) {
  const choices = (draft.flow_state.choices ?? {}) as { version_id?: string };
  return <LabelStep versionId={choices.version_id ?? null} onStarted={onAdvance} />;
}

const panel: GuidedStepPanel = { step: 'label', order: 5, Component: LabelGuidedStep };
export default panel;

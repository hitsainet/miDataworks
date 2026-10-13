// Renders every discovered panel for the current step, or names the feature that builds it.
import { EmptyState } from '@/components/common/EmptyState';
import type { RecipeDraft } from '@/types/recipes';

import { STEP_OWNERS, discoveredStepPanels } from './guidedSteps';
import type { GuidedStepPanel } from './guidedSteps';

export function GuidedStepHost({ step, draft, onAdvance, panels = discoveredStepPanels() }: { step: string; draft: RecipeDraft; onAdvance: () => void; panels?: GuidedStepPanel[] }) {
  const here = panels.filter((p) => p.step === step);
  if (here.length === 0) {
    return (
      <EmptyState
        title={`The ${step} step is not built yet`}
        description={`Its panel comes from ${STEP_OWNERS[step] ?? 'the feature that owns it'}. Your progress on the other steps is saved.`}
      />
    );
  }
  return (
    <div className="space-y-5" data-testid={`guided-step-${step}`}>
      {here.map(({ Component }, i) => <Component key={i} draft={draft} onAdvance={onAdvance} />)}
    </div>
  );
}

// Renders every discovered panel for the current step. Every step the backend names is served
// (guided.test.tsx derives the list from GET /datasets/meta's fixture, which a backend test pins to
// GUIDED_STEPS), so the branch below is a fault, not a missing feature: it says so and never claims
// that the build lacks the step (that copy was removed 2026-10-17).
import { EmptyState } from '@/components/common/EmptyState';
import type { RecipeDraft } from '@/types/recipes';

import { discoveredStepPanels } from './guidedSteps';
import type { GuidedStepPanel } from './guidedSteps';

export function GuidedStepHost({ step, draft, onAdvance, panels = discoveredStepPanels() }: { step: string; draft: RecipeDraft; onAdvance: () => void; panels?: GuidedStepPanel[] }) {
  const here = panels.filter((p) => p.step === step);
  if (here.length === 0) {
    return (
      <EmptyState
        title={`The ${step} step could not be shown`}
        description="No screen is registered for this step, which is a fault in miDataworks. Reload the page; if it persists, report it. Your progress on the other steps is saved."
      />
    );
  }
  return (
    <div className="stack-y-5" data-testid={`guided-step-${step}`}>
      {here.map(({ Component }, i) => <Component key={i} draft={draft} onAdvance={onAdvance} />)}
    </div>
  );
}

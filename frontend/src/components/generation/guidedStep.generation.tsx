// Feature 007's guided-flow panel (P-23): the Assemble step links to the Generation screen; the flow
// itself stays 002's.
import type { GuidedStepPanel } from '@/components/guided/guidedSteps';

function GenerationLink() {
  return (
    <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="guided-generation-link">
      Add generated rows on the{' '}
      <a href="#/generation" className="text-indigo-600 dark:text-indigo-400 underline focus-visible:ring-2 focus-visible:ring-indigo-500">
        Generation screen
      </a>{' '}
      — from a version that already has a held-out split.
    </p>
  );
}

const panel: GuidedStepPanel = { step: 'assemble', order: 900, Component: GenerationLink };

export default panel;

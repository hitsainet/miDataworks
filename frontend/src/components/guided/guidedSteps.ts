// Guided-flow step panels (FTDD 002 section 6.3): files matching src/components/**/guidedStep.*.tsx
// default-export a GuidedStepPanel. The seven step names come from GET /datasets/meta, never from a
// frontend constant, so a step added in the backend appears with no edit here.
import type { ComponentType } from 'react';

import type { RecipeDraft } from '@/types/recipes';

export interface GuidedStepProps {
  draft: RecipeDraft;
  onAdvance: () => void;
}

export interface GuidedStepPanel {
  step: string;
  order: number;
  Component: ComponentType<GuidedStepProps>;
}

export function collectStepPanels(modules: Record<string, unknown>): GuidedStepPanel[] {
  return Object.values(modules)
    .map((m) => (m as { default?: GuidedStepPanel }).default)
    .filter((p): p is GuidedStepPanel => Boolean(p && p.step && p.Component))
    .sort((a, b) => a.order - b.order);
}

export const discoveredStepPanels = (): GuidedStepPanel[] =>
  collectStepPanels(import.meta.glob('../**/guidedStep.*.tsx', { eager: true }));

/** Steps the backend names that no discovered panel serves: reported, never silently blank. */
export function stepsWithoutPanels(steps: string[], panels: GuidedStepPanel[]): string[] {
  const served = new Set(panels.map((p) => p.step));
  return steps.filter((s) => !served.has(s));
}

/** Who builds each step's panel (FR-002.48, P-23), for the empty state's copy only. */
export const STEP_OWNERS: Record<string, string> = {
  import: 'feature 001 (Sources and import)',
  goal: 'feature 002',
  profile: 'feature 004 (Curation)',
  curate: 'feature 004 (Curation)',
  label: 'feature 005 (Labeling)',
  assemble: 'feature 004 (Curation)',
  export: 'feature 008 (Publishing)',
};

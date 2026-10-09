// Dataset card slots: feature 008 supplies publish state and the "Evaluation only" pill (P-14,
// X-11) by adding src/components/**/datasetCardSlot.*.tsx. Until it does, a card says "Not
// published" in amber — the honest reading of "no publish recorded".
import type { ComponentType } from 'react';

import type { DatasetSummary } from '@/types/versions';

export interface DatasetCardSlot {
  id: string;
  order: number;
  Component: ComponentType<{ dataset: DatasetSummary }>;
}

export function collectCardSlots(modules: Record<string, unknown>): DatasetCardSlot[] {
  return Object.values(modules)
    .map((m) => (m as { default?: DatasetCardSlot }).default)
    .filter((s): s is DatasetCardSlot => Boolean(s && s.id && s.Component))
    .sort((a, b) => a.order - b.order);
}

export const discoveredCardSlots = (): DatasetCardSlot[] =>
  collectCardSlots(import.meta.glob('../**/datasetCardSlot.*.tsx', { eager: true }));

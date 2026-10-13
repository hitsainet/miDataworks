// Which component renders each built screen. A screen with no entry shows Foundation's empty state
// naming the feature that builds it. A feature REPLACES the empty state by adding its line here —
// and src/config/panelComponents.test.tsx turns red when a built screen's line is removed.
import type { ComponentType } from 'react';

import { CalibrationPanel } from '@/components/panels/CalibrationPanel';
import { DatasetsPanel } from '@/components/panels/DatasetsPanel';
import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { NewDatasetPanel } from '@/components/panels/NewDatasetPanel';
import { OperatorsPanel } from '@/components/operators/OperatorsPanel';
import { PublishPanel } from '@/components/panels/PublishPanel';
import { RecipesPanel } from '@/components/panels/RecipesPanel';
import { ReviewPanel } from '@/components/panels/ReviewPanel';
import { SettingsPanel } from '@/components/panels/SettingsPanel';
import { VersionDetailPanel } from '@/components/panels/VersionDetailPanel';
import type { ActivePanel, PanelDef } from '@/config/panels';

export const PANEL_COMPONENTS: Partial<Record<ActivePanel, ComponentType<{ panel: PanelDef }>>> = {
  datasets: DatasetsPanel,
  'new-dataset': NewDatasetPanel,
  version: VersionDetailPanel,
  recipes: RecipesPanel,
  'label-runs': LabelRunsPanel,
  calibration: CalibrationPanel,
  review: ReviewPanel,
  'detector-sets': DetectorSetsPanel,
  generation: GenerationPanel,
  operators: OperatorsPanel,
  publish: PublishPanel,
  settings: SettingsPanel,
};

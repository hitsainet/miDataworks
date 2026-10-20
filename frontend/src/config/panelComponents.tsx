// Which component renders each screen. Every screen in config/panels.ts is built, so the map is
// COMPLETE: the type (a full Record, not Partial) refuses a missing entry at compile time, and
// src/config/panelComponents.test.tsx checks it at run time from PANELS. There is no "not built
// yet" fallback any more (removed 2026-10-17: every screen had shipped, so it could only lie).
import type { ComponentType } from 'react';

import { HelpPanel } from '@/components/help/HelpPanel';
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

export const PANEL_COMPONENTS: Record<ActivePanel, ComponentType<{ panel: PanelDef }>> = {
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
  help: HelpPanel,
};

// Origin: miStudio (Onegaishimas/miStudio) frontend/src/config/panels.ts @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: ONE registry, a type derived from it, and a narrowing guard
// for untrusted strings (persisted panel, URL hash). Changed: the registry also carries each
// screen's label, title, icon and owning feature, so navigation, routing and persisted state all
// read one list (miStudio's triplicated list once made a panel vanish on reload with no type
// error). Nav order is array order.
import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  Blocks,
  Database,
  FileCode2,
  ListChecks,
  Radar,
  Scale,
  Settings,
  Sparkles,
  Tags,
  Upload,
  UserCheck,
} from 'lucide-react';

export interface PanelDef {
  id: string;
  /** Sidebar label. */
  label: string;
  /** Page title, sentence case (R-03.62). */
  title: string;
  /** One sentence under the title. */
  subtitle: string;
  icon: LucideIcon;
  /** The feature that builds the screen; Foundation registers it as an empty state. */
  feature: string;
  /** Rendered in the sidebar foot instead of the main list. */
  foot?: boolean;
}

/** Every R-03.60 screen. Order is nav order. */
export const PANELS = [
  { id: 'datasets', label: 'Datasets', title: 'Datasets', subtitle: 'Import from Hugging Face, curate and label into versions, publish back to the Hub', icon: Database, feature: '001 Sources and import' },
  { id: 'new-dataset', label: 'New dataset', title: 'New dataset', subtitle: 'Seven guided steps from a Hugging Face dataset to a published version.', icon: ListChecks, feature: '002 Versions, recipes and provenance' },
  { id: 'version', label: 'Version detail', title: 'Version detail', subtitle: 'What one version holds, how it was built and why rows left it.', icon: Activity, feature: '002 Versions, recipes and provenance' },
  { id: 'recipes', label: 'Recipes', title: 'Recipes', subtitle: 'Saved, hashed operator pipelines that rebuild a version exactly.', icon: FileCode2, feature: '002 Versions, recipes and provenance' },
  { id: 'label-runs', label: 'Label runs', title: 'Label runs', subtitle: 'Classifier and judge runs: who labelled what, with which model and question.', icon: Tags, feature: '005 Labeling and endpoints' },
  { id: 'calibration', label: 'Calibration', title: 'Calibration', subtitle: 'How far each labeller agrees with human labels, and whether it passes the gate.', icon: Scale, feature: '006 Calibration and human review' },
  { id: 'review', label: 'Review', title: 'Review', subtitle: 'Human decisions on sampled rows, for miDataworks and miForge.', icon: UserCheck, feature: '006 Calibration and human review' },
  { id: 'generation', label: 'Generation', title: 'Generation', subtitle: 'Synthetic rows and steered pairs generated through an endpoint.', icon: Sparkles, feature: '007 Synthetic generation and steered pairs' },
  { id: 'detector-sets', label: 'Detector sets', title: 'Detector sets', subtitle: 'Train, test, out-of-distribution and calibration sets for miStudio probes.', icon: Radar, feature: '009 Detector sets and the miStudio loop' },
  { id: 'operators', label: 'Operators', title: 'Operators', subtitle: 'Every curation step available, across native, Data-Juicer and Data Designer.', icon: Blocks, feature: '003 Operator framework and engines' },
  { id: 'publish', label: 'Publish and export', title: 'Publish and export', subtitle: 'Push versions to the Hugging Face Hub and export files, with checks before and after.', icon: Upload, feature: '008 Publishing, export and handoff contract' },
  { id: 'settings', label: 'Settings', title: 'Settings', subtitle: 'Where miDataworks finds models and the other apps, and who may drive it.', icon: Settings, feature: 'Foundation', foot: true },
] as const satisfies readonly PanelDef[];

export type ActivePanel = (typeof PANELS)[number]['id'];

export const PANEL_IDS: readonly ActivePanel[] = PANELS.map((p) => p.id);

export const DEFAULT_PANEL: ActivePanel = 'datasets';

/** Narrow an untrusted string (localStorage, a URL hash) to a real panel. */
export function isActivePanel(value: string | null | undefined): value is ActivePanel {
  return value != null && (PANEL_IDS as readonly string[]).includes(value);
}

export function getPanel(id: ActivePanel): PanelDef {
  return PANELS.find((p) => p.id === id) as PanelDef;
}

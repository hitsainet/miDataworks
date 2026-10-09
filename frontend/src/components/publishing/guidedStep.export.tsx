// The guided flow's Export step (P-23: 002 owns the flow and discovers this file; feature 008 owns the
// panel). Publishing needs the publish checks, the card editor and the Hub token, which live on the
// Publish and export screen, so this step hands the chosen version to that screen instead of
// duplicating it. Until 2026-10-07 no file served this step and the flow ended on "The export step is
// not built yet".
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useVersionsStore } from '@/stores/versionsStore';

function ExportStep({ draft }: GuidedStepProps) {
  const versionId = ((draft.flow_state.choices ?? {}) as { version_id?: string }).version_id;
  const version = useVersionsStore((s) => (versionId ? s.versions[versionId] : undefined));
  return (
    <div data-testid="guided-export-step">
      <h2 className="font-semibold mb-1">Publish or export</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
        Push the version to the Hugging Face Hub, or export it for TRL or miForge. The checks run
        before anything leaves this machine.
      </p>
      {versionId ? (
        <p className="text-sm text-slate-600 dark:text-slate-300 mb-3">
          Version: <span className="font-medium">{version ? `v${version.number}` : versionId}</span>
        </p>
      ) : (
        <p className="text-sm mb-3">Choose or build a version first; publishing reads a version.</p>
      )}
      <a
        href="#/publish"
        className="inline-flex items-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500"
      >
        Open Publish and export
      </a>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'export', order: 7, Component: ExportStep };
export default panel;

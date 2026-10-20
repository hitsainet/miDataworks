// The guided flow's Profile step (FR-004.44; P-23: 002 owns the flow and discovers this file).
// The profile reads a built version. Before the first build there is none, so the step says so and
// offers the way there (novice walk 2026-10-19, finding 1: it said "build a version first" with no
// way to do either, and the rail then marked it done).
import { Button } from '@/components/common/Button';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useDraftsStore } from '@/stores/draftsStore';
import { useVersionsStore } from '@/stores/versionsStore';

import { ProfileSlot } from './versionDetailSlot.profile';

function ProfileStep({ draft, onAdvance }: GuidedStepProps) {
  const versionId = ((draft.flow_state.choices ?? {}) as { version_id?: string }).version_id;
  const version = useVersionsStore((s) => (versionId ? s.versions[versionId] : undefined));
  const setStep = useDraftsStore((s) => s.setStep);
  return (
    <div>
      <h2 className="font-semibold mb-1">Profile the data</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">Lengths, duplicates and clusters, each with its scale and sample size.</p>
      {version ? (
        <ProfileSlot version={version} />
      ) : (
        <p className="text-sm" data-testid="profile-needs-version">
          The profile reads a built version, and this dataset has none yet. Choose its steps and build it on the Curate step; the profile then appears here.
        </p>
      )}
      <div className="flex justify-end gap-2 mt-4">
        {version ? <Button onClick={onAdvance}>Continue to curation</Button> : <Button onClick={() => setStep('curate')}>Go to Curate to build the version</Button>}
      </div>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'profile', order: 2, Component: ProfileStep };
export default panel;

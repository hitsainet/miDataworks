// The guided flow's Profile step (FR-004.44; P-23: 002 owns the flow and discovers this file).
import { Button } from '@/components/common/Button';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useVersionsStore } from '@/stores/versionsStore';

import { ProfileSlot } from './versionDetailSlot.profile';

function ProfileStep({ draft, onAdvance }: GuidedStepProps) {
  const versionId = ((draft.flow_state.choices ?? {}) as { version_id?: string }).version_id;
  const version = useVersionsStore((s) => (versionId ? s.versions[versionId] : undefined));
  return (
    <div>
      <h2 className="font-semibold mb-1">Profile the data</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">Lengths, duplicates and clusters, each with its scale and sample size.</p>
      {version ? <ProfileSlot version={version} /> : <p className="text-sm">Choose or build a version first; the profile reads a version.</p>}
      <div className="flex justify-end mt-4">
        <Button onClick={onAdvance}>Continue to curation</Button>
      </div>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'profile', order: 2, Component: ProfileStep };
export default panel;

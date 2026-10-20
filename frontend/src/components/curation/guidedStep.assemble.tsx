// The guided flow's Assemble step (FR-004.44): split counts, the shortcut audit and the leakage check
// for the labeled version, with the cell-balanced build one button away.
import { Button } from '@/components/common/Button';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useVersionsStore } from '@/stores/versionsStore';

import { LeakageSlot } from './versionDetailSlot.leakage';
import { ShortcutAuditSlot } from './versionDetailSlot.shortcutAudit';

function AssembleStep({ draft, onAdvance }: GuidedStepProps) {
  const versionId = ((draft.flow_state.choices ?? {}) as { version_id?: string }).version_id;
  const version = useVersionsStore((s) => (versionId ? s.versions[versionId] : undefined));
  return (
    <div>
      <h2 className="font-semibold mb-1">Assemble</h2>
      {version ? (
        <div className="space-y-4">
          <p className="text-sm">
            Splits: {version.splits.map((s) => `${s.name} ${s.rows.toLocaleString('en-US')}${s.held_out ? ' (held out)' : ''}`).join(', ') || 'none'}.
          </p>
          <ShortcutAuditSlot version={version} />
          <LeakageSlot version={version} />
        </div>
      ) : (
        <p className="text-sm">Build the labeled version first; the audit and the leakage check read it.</p>
      )}
      <div className="flex justify-end mt-4">
        <Button onClick={onAdvance}>Continue to export</Button>
      </div>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'assemble', order: 6, Component: AssembleStep };
export default panel;

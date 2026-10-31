// Feature 007's version-detail slot (FTDD 002 section 6.3): the diversity report of a version that
// holds generated rows (it binds a generation run).
import type { VersionSlot } from '@/components/versions/versionSlots';
import type { Version } from '@/types/versions';

import { DiversityPanel } from './DiversityPanel';

function DiversitySlot({ version }: { version: Version }) {
  return <DiversityPanel versionId={version.id} />;
}

const slot: VersionSlot = {
  id: 'diversity',
  order: 450,
  title: 'Diversity of generated rows',
  applies: (version) => version.bindings.some((b) => b.kind === 'generation_run'),
  Component: DiversitySlot,
};

export default slot;

// Renders every discovered Version detail slot that applies to this version.
import type { Version } from '@/types/versions';

import { discoveredSlots } from './versionSlots';
import type { VersionSlot } from './versionSlots';

export function VersionSlotHost({ version, slots = discoveredSlots() }: { version: Version; slots?: VersionSlot[] }) {
  const applicable = slots.filter((s) => s.applies(version));
  if (applicable.length === 0) return null;
  return (
    <div className="grid gap-5 lg:grid-cols-2 mb-5" data-testid="version-slots">
      {applicable.map(({ id, title, Component }) => (
        <section key={id} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 min-w-0" data-testid={`slot-${id}`}>
          <h2 className="font-semibold mb-3">{title}</h2>
          <Component version={version} />
        </section>
      ))}
    </div>
  );
}

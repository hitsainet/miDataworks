// The two-column dataset card grid (FR-002.44), mounted on the Datasets screen below feature 001's
// import form and sources list (T-01).
import { ShowArchivedToggle } from '@/components/common/ArchiveControls';
import type { DatasetSummary } from '@/types/versions';

import { DatasetCard } from './DatasetCard';
import { discoveredCardSlots } from './datasetCardSlots';
import type { DatasetCardSlot } from './datasetCardSlots';

export interface DatasetArchiving {
  showArchived: boolean;
  setShowArchived: (value: boolean) => void;
  onArchive: (d: DatasetSummary) => Promise<unknown>;
  onUnarchive: (d: DatasetSummary) => Promise<unknown>;
}

export function DatasetCardGrid({
  datasets,
  onOpen,
  slots = discoveredCardSlots(),
  archiving,
}: {
  datasets: DatasetSummary[];
  onOpen: (d: DatasetSummary) => void;
  slots?: DatasetCardSlot[];
  /** Archive (2026-10-20): the toggle and the per-card action; absent where a grid is read-only. */
  archiving?: DatasetArchiving;
}) {
  return (
    <section aria-labelledby="your-datasets">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 id="your-datasets" className="text-base font-semibold">Your datasets ({datasets.length})</h2>
        {archiving && <ShowArchivedToggle checked={archiving.showArchived} onChange={archiving.setShowArchived} testId="show-archived-datasets" />}
      </div>
      {datasets.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {archiving?.showArchived ? 'No datasets, archived or not. Start one with New dataset.' : 'No datasets yet. Start one with New dataset.'}
        </p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {datasets.map((d) => (
            <DatasetCard
              key={d.id}
              dataset={d}
              slots={slots}
              onOpen={onOpen}
              onArchive={archiving ? () => archiving.onArchive(d) : undefined}
              onUnarchive={archiving ? () => archiving.onUnarchive(d) : undefined}
            />
          ))}
        </div>
      )}
    </section>
  );
}

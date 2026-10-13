// The two-column dataset card grid (FR-002.44), mounted on the Datasets screen below feature 001's
// import form and sources list (T-01).
import type { DatasetSummary } from '@/types/versions';

import { DatasetCard } from './DatasetCard';
import { discoveredCardSlots } from './datasetCardSlots';
import type { DatasetCardSlot } from './datasetCardSlots';

export function DatasetCardGrid({ datasets, onOpen, slots = discoveredCardSlots() }: { datasets: DatasetSummary[]; onOpen: (d: DatasetSummary) => void; slots?: DatasetCardSlot[] }) {
  return (
    <section aria-labelledby="your-datasets">
      <h2 id="your-datasets" className="text-base font-semibold mb-3">Your datasets ({datasets.length})</h2>
      {datasets.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No datasets yet. Start one with New dataset.</p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">{datasets.map((d) => <DatasetCard key={d.id} dataset={d} slots={slots} onOpen={onOpen} />)}</div>
      )}
    </section>
  );
}

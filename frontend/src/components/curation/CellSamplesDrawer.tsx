// Thirty seeded rows of one (value x label) cell (FR-004.32; records/crosstab_samples.md).
import { useEffect } from 'react';

import { Button } from '@/components/common/Button';
import { cellKey, useCurationStore } from '@/stores/curationStore';

interface Props {
  versionId: string;
  column: string;
  value: string;
  label: string;
  onClose: () => void;
}

export function CellSamplesDrawer({ versionId, column, value, label, onClose }: Props) {
  const page = useCurationStore((s) => s.cellSamples[cellKey(versionId, column, value, label)]);
  const fetchCellSamples = useCurationStore((s) => s.fetchCellSamples);
  useEffect(() => {
    void fetchCellSamples(versionId, column, value, label);
  }, [versionId, column, value, label, fetchCellSamples]);
  return (
    <aside aria-label="Cell samples" className="rounded border border-slate-300 dark:border-slate-600 p-3 mt-3">
      <div className="flex justify-between items-center">
        <h4 className="text-sm font-semibold">
          {column} = {value} → {label}
        </h4>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close the samples
        </Button>
      </div>
      {!page && <p className="text-xs text-slate-500 dark:text-slate-400">Loading samples…</p>}
      {page && (
        <>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {page.total} seeded rows (seed {page.seed}) of {page.cell_rows ?? page.total} in this cell.
          </p>
          <ul className="text-xs mt-1 space-y-1">
            {page.rows.map((r) => (
              <li key={`${r.row_key}:${r.occurrence}`} className="font-mono break-words">
                {Object.values(r.excerpt).join(' · ')}
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

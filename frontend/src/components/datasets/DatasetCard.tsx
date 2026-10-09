// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/datasets/DatasetCard.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: an icon, the name, a meta line, size and rows, a state pill on
// the right, the whole card as the click target. Changed: head version number, target type, parent
// and warnings (FR-002.44); publish state comes from feature 008's slot, "Not published" in amber
// until it reports one; no download progress (imports live in feature 001's sources list).
import { Database } from 'lucide-react';

import type { DatasetSummary } from '@/types/versions';
import { formatBytes } from '@/utils/format';

import type { DatasetCardSlot } from './datasetCardSlots';

export function DatasetCard({ dataset, slots, onOpen }: { dataset: DatasetSummary; slots: DatasetCardSlot[]; onOpen: (d: DatasetSummary) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(dataset)}
      className="text-left w-full rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 hover:border-indigo-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
      data-testid="dataset-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex gap-3 min-w-0">
          <Database className="w-[18px] h-[18px] mt-0.5 text-slate-500 dark:text-slate-400 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <div className="font-semibold flex flex-wrap items-center gap-2">
              {dataset.name}
              <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{dataset.head_number ? `v${dataset.head_number}` : 'no version yet'}</span>
            </div>
            <div className="text-xs mt-1 text-slate-500 dark:text-slate-400">
              {dataset.target_type} · {dataset.versions} version{dataset.versions === 1 ? '' : 's'}{dataset.parent_version_id ? ' · built from a parent version' : ''}
            </div>
            {dataset.rows !== null && (
              <div className="text-xs mt-1 text-slate-500 dark:text-slate-400">
                Size: <b className="text-slate-800 dark:text-slate-100">{formatBytes(dataset.bytes ?? 0)}</b> · Rows: <b className="text-slate-800 dark:text-slate-100">{dataset.rows.toLocaleString('en-US')}</b>
              </div>
            )}
            {slots.length > 0 ? slots.map(({ id, Component }) => <Component key={id} dataset={dataset} />) : (
              <div className="text-xs mt-1 text-amber-700 dark:text-amber-400">Not published</div>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0 text-xs">
          <span className={dataset.state === 'ready' ? 'text-green-700 dark:text-green-400' : 'text-slate-500 dark:text-slate-400'}>{dataset.state === 'ready' ? 'Ready' : 'Empty'}</span>
          {dataset.warnings_count > 0 && <span className="text-amber-700 dark:text-amber-400">{dataset.warnings_count} warning{dataset.warnings_count === 1 ? '' : 's'}</span>}
        </div>
      </div>
    </button>
  );
}

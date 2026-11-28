// Version detail slot: the source-against-label cross-tab (FR-004.32; P-23, X-04 — 004 builds it,
// 002's slot hosts it). Reads the same audit the shortcut-audit slot reads.
import { useState } from 'react';

import { toggleOpen } from '@/components/common/DetailPopup';
import type { VersionSlot } from '@/components/versions/versionSlots';
import { useCurationStore } from '@/stores/curationStore';
import type { Version } from '@/types/versions';

import { CellSamplesDrawer } from './CellSamplesDrawer';
import { CrossTabTable } from './CrossTabTable';

export function CrossTabSlot({ version }: { version: Version }) {
  const view = useCurationStore((s) => s.auditsByVersion[version.id]);
  const columns = view?.audit.columns.filter((c) => c.kind === 'metadata' || c.column === '_dw_source_id') ?? [];
  const preferred = columns.find((c) => /source/.test(c.column))?.column ?? columns[0]?.column ?? '';
  const [column, setColumn] = useState('');
  // The open cell's count closes its samples; another cell switches (operator, 2026-10-31).
  const [cellId, setCellId] = useState<string | null>(null);
  const cell = cellId === null ? null : (JSON.parse(cellId) as { value: string; label: string });
  if (!view) return <p className="text-sm text-slate-500 dark:text-slate-400">The cross-tab appears once the shortcut audit has run.</p>;
  const shown = column || preferred;
  return (
    <div>
      <label className="text-xs">
        <span className="text-slate-500 dark:text-slate-400 mr-2">Column</span>
        <select
          aria-label="Cross-tab column"
          value={shown}
          onChange={(e) => setColumn(e.target.value)}
          className="rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs"
        >
          {columns.map((c) => (
            <option key={c.column} value={c.column}>{c.column}</option>
          ))}
        </select>
      </label>
      <div className="mt-2">
        <CrossTabTable audit={view.audit} column={shown} onCell={(value, label) => setCellId(toggleOpen(cellId, JSON.stringify({ value, label })))} />
      </div>
      {cell && <CellSamplesDrawer versionId={version.id} column={shown} value={cell.value} label={cell.label} onClose={() => setCellId(null)} />}
    </div>
  );
}

const slot: VersionSlot = { id: 'cross-tab', order: 110, title: 'Source against label', applies: () => true, Component: CrossTabSlot };
export default slot;

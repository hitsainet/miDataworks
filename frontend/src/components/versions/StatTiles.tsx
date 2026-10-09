// Four stat tiles for a version (FR-002.42). The tiles are measured from the version itself;
// figures other features own (the shortcut audit, per-class counts from labels) arrive through
// Version detail slots, so no tile here shows a number this feature did not compute.
import type { DatasetsMeta, Version } from '@/types/versions';
import { formatBytes, formatCount } from '@/utils/format';

export function tilesFor(version: Version, meta: DatasetsMeta | null): Array<{ label: string; value: string; note: string }> {
  const dropped = version.drop_summary.reduce((n, s) => n + s.dropped, 0);
  const rowsIn = version.drop_summary[0]?.rows_in ?? version.total_rows;
  const heldOut = version.splits.filter((s) => s.held_out);
  const content = Object.entries(version.column_roles).filter(([, r]) => r === 'content').map(([c]) => c);
  const defaults = meta?.default_content_columns[version.target_type] ?? [];
  return [
    { label: 'Rows', value: version.total_rows.toLocaleString('en-US'), note: `${formatBytes(version.total_bytes)} across ${formatCount(version.splits.length, 'split')}` },
    { label: 'Dropped by the recipe', value: formatCount(dropped, 'row'), note: `of ${formatCount(rowsIn, 'row')} in` },
    { label: 'Held out', value: formatCount(heldOut.reduce((n, s) => n + s.rows, 0), 'row'), note: heldOut.length ? `split ${heldOut.map((s) => s.name).join(', ')}` : 'no held-out split' },
    { label: 'Keyed on', value: content.join(', ') || '—', note: defaults.length ? `${version.target_type} default: ${defaults.join(' or ')}` : `${version.target_type}: the detected text column` },
  ];
}

export function StatTiles({ version, meta }: { version: Version; meta: DatasetsMeta | null }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5" data-testid="stat-tiles">
      {tilesFor(version, meta).map((t) => (
        <div key={t.label} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-3">
          <div className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t.label}</div>
          <div className="text-xl font-semibold tabular-nums truncate" title={t.value}>{t.value}</div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">{t.note}</div>
        </div>
      ))}
    </div>
  );
}

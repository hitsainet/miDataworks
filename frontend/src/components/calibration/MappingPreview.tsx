// What a mapping yields before anything is saved: counts, the sorted-ratings warning, refusals.
import type { CalibrationSetPreview } from '@/types/calibration';

import { fmtInt } from './format';

export function MappingPreview({ preview, error }: { preview: CalibrationSetPreview | null; error: string | null }) {
  if (error) return <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="mapping-error">{error}</p>;
  if (!preview) return <p className="text-sm text-slate-500 dark:text-slate-400">Fill in the human-label column and both cut points to preview.</p>;
  const c = preview.counts;
  return (
    <div data-testid="mapping-preview" className="text-sm space-y-1">
      <p className="tabular-nums">
        {fmtInt(c.rows)} rows: {fmtInt(c.positives)} positive, {fmtInt(c.negatives)} negative, {fmtInt(c.excluded)} excluded by the cut points
        {c.references ? `, ${fmtInt(c.references)} reference rows` : ''}{c.groups ? ` in ${fmtInt(c.groups)} groups` : ''}.
      </p>
      {preview.warnings.map((w) => (
        <p key={w} role="note" className="rounded border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-2 py-1 text-xs" data-testid="sorted-warning">{w}</p>
      ))}
    </div>
  );
}

// The threshold control (FR-003.20, FR-003.21; R-03.16). A histogram of the operator's statistic
// on its own scale, a cutoff per threshold parameter (two for a band), and a live count and
// excerpts of the rows that would drop — recomputed in the browser with useMemo, so moving the
// slider sends NO request. A constant statistic shows one bar and says the sample cannot inform
// the cut. Every number names its sample.
import { useMemo, useState } from 'react';
import { Bar, BarChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { Button } from '@/components/common/Button';
import type { StatisticValue, ThresholdStatistic } from '@/types/operators';

export const BINS = 20;
export const MAX_EXCERPTS = 10;

export interface Cut {
  param: string;
  drop_when: 'below' | 'above';
  value: number;
}

export function wouldDrop(value: number | null, cuts: Cut[]): boolean {
  if (value === null) return false;
  return cuts.some((c) => (c.drop_when === 'below' ? value < c.value : value > c.value));
}

export function histogram(values: number[], bins = BINS): Array<{ mid: number; count: number }> {
  if (!values.length) return [];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (lo === hi) return [{ mid: lo, count: values.length }];
  const width = (hi - lo) / bins;
  const counts = new Array<number>(bins).fill(0);
  for (const v of values) counts[Math.min(bins - 1, Math.floor((v - lo) / width))] += 1;
  return counts.map((count, i) => ({ mid: lo + width * (i + 0.5), count }));
}

function startValue(t: ThresholdStatistic, lo: number, hi: number): number {
  if (typeof t.current === 'number' && Number.isFinite(t.current)) return Math.min(Math.max(t.current, lo), hi);
  return t.drop_when === 'below' ? lo : hi;
}

export function ThresholdControl({
  thresholds,
  sampleSize,
  onCommit,
}: {
  thresholds: ThresholdStatistic[];
  sampleSize: number;
  onCommit?: (values: Record<string, number>) => void;
}) {
  const first = thresholds[0];
  const values: StatisticValue[] = useMemo(() => first?.values ?? [], [first]);
  const numbers = useMemo(() => values.map((v) => v.value).filter((v): v is number => v !== null), [values]);
  const lo = numbers.length ? Math.min(...numbers) : 0;
  const hi = numbers.length ? Math.max(...numbers) : 0;
  const [cuts, setCuts] = useState<Cut[]>(() =>
    thresholds.map((t) => ({ param: t.param, drop_when: t.drop_when, value: startValue(t, lo, hi) })),
  );
  const bars = useMemo(() => histogram(numbers), [numbers]);
  const dropped = useMemo(() => values.filter((v) => wouldDrop(v.value, cuts)), [values, cuts]);
  if (!first) return null;
  const constant = thresholds.some((t) => t.constant) || lo === hi;
  const share = values.length ? ((100 * dropped.length) / values.length).toFixed(1) : '0.0';
  const integer = numbers.every((n) => Number.isInteger(n));
  return (
    <div className="space-y-3" data-testid="threshold-control">
      <div className="text-sm font-medium text-slate-700 dark:text-slate-300">
        {first.statistic} <span className="text-slate-500 dark:text-slate-400">({first.unit})</span>
      </div>
      <div className="h-40" aria-label={`Histogram of ${first.statistic} over ${values.length} sample rows`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} margin={{ top: 4, right: 8, bottom: 16, left: 0 }}>
            <XAxis
              dataKey="mid"
              type="number"
              domain={[lo, hi === lo ? lo + 1 : hi]}
              tickFormatter={(v: number) => (integer ? String(Math.round(v)) : v.toFixed(2))}
              label={{ value: `${first.statistic} (${first.unit})`, position: 'insideBottom', offset: -8, fontSize: 11 }}
              fontSize={11}
            />
            <YAxis allowDecimals={false} fontSize={11} width={32} />
            {/* Recharts 3 types the value as possibly undefined; a bar's count is always a number. */}
            <Tooltip formatter={(v) => [`${v ?? 0} rows`, 'Sample rows']} />
            <Bar dataKey="count" fill="#818cf8" isAnimationActive={false} />
            {cuts.map((c) => (
              <ReferenceLine key={c.param} x={c.value} stroke="#ef4444" strokeDasharray="4 2" label={{ value: c.param, fontSize: 10 }} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {constant ? (
        <p className="text-sm text-amber-600 dark:text-amber-400" data-testid="constant-statistic">
          Every sample row has the same {first.statistic} ({lo} {first.unit}). This sample cannot inform this cut; preview a
          larger or different sample before choosing it.
        </p>
      ) : null}
      {cuts.map((c, i) => (
        <label key={c.param} className="flex items-center gap-3 text-sm text-slate-700 dark:text-slate-300">
          <span className="w-40 font-mono">
            {c.param} {c.drop_when === 'below' ? '(drop below)' : '(drop above)'}
          </span>
          <input
            type="range"
            aria-label={`${c.param} cutoff`}
            min={lo}
            max={hi === lo ? lo + 1 : hi}
            step={integer ? 1 : (hi - lo) / 100 || 0.01}
            value={c.value}
            disabled={constant}
            onChange={(e) => {
              const next = [...cuts];
              next[i] = { ...c, value: Number(e.target.value) };
              setCuts(next);
            }}
            className="flex-1 accent-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-400"
          />
          <span className="w-24 text-right font-mono">
            {integer ? c.value : c.value.toFixed(3)} {first.unit}
          </span>
        </label>
      ))}
      <p className="text-sm text-slate-700 dark:text-slate-300" data-testid="drop-count">
        Drops {dropped.length} of {values.length} sample rows ({share}%). Sample of {sampleSize} rows; any full-set share is an
        estimate from this sample.
      </p>
      {dropped.length ? (
        <ul className="space-y-1 text-xs" data-testid="drop-excerpts">
          {dropped.slice(0, MAX_EXCERPTS).map((v) => (
            <li key={`${v.row_key}:${v.occurrence}`} className="font-mono text-slate-600 dark:text-slate-400">
              {v.value} {first.unit} — {v.excerpt || '(empty)'}
            </li>
          ))}
        </ul>
      ) : null}
      {onCommit ? (
        <Button variant="secondary" size="sm" onClick={() => onCommit(Object.fromEntries(cuts.map((c) => [c.param, c.value])))}>
          Use this cut
        </Button>
      ) : null}
    </div>
  );
}

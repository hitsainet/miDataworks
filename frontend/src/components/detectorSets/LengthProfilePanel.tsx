// Both length profiles on one axis, the five quantiles and n for each, and the overlap note
// (FR-009.9 - FR-009.11). Shares, not counts, so sets of different sizes compare.
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { CheckOutcome, LengthProfile } from '@/types/detectorSets';

import { count } from './format';

function shares(p: LengthProfile): Array<{ x: number; share: number }> {
  const total = p.histogram.counts.reduce((a, b) => a + b, 0) || 1;
  return p.histogram.counts.map((c, i) => ({ x: Math.round(p.histogram.edges[i]), share: c / total }));
}

const q = (x: number | undefined) => (x == null ? '—' : Number(x.toFixed(1)).toLocaleString('en-US'));

function Quantiles({ name, p }: { name: string; p: LengthProfile }) {
  return (
    <div className="text-xs tabular-nums" data-testid={`profile-${name}`}>
      <div className="font-medium">{name}: n = {count(p.n)} rows, characters</div>
      <div className="text-slate-500 dark:text-slate-400">
        p05 {q(p.quantiles.p05)} · p25 {q(p.quantiles.p25)} · median {q(p.quantiles.p50)} · p75 {q(p.quantiles.p75)} · p95 {q(p.quantiles.p95)}
      </div>
      {p.finest_fpr != null && name === 'Calibration negatives' && (
        <div className="text-slate-500 dark:text-slate-400">
          Finest false positive rate these rows afford: 1 / {count(p.n)} = {(p.finest_fpr * 100).toFixed(3)}%. miStudio may cap the rows it uses (calibration_max_rows).
        </div>
      )}
    </div>
  );
}

export function LengthProfilePanel({
  calibration,
  monitored,
  check,
}: {
  calibration: LengthProfile | null;
  monitored: LengthProfile | null;
  check: CheckOutcome | null;
}) {
  if (!calibration || !monitored) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">Bind calibration negatives and name the monitored text to compare their lengths.</p>;
  }
  const a = shares(calibration);
  const b = shares(monitored);
  return (
    <div data-testid="length-profiles">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="h-36">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={a}><XAxis dataKey="x" fontSize={10} /><YAxis hide /><Tooltip /><Bar dataKey="share" fill="#6366f1" name="Calibration negatives" /></BarChart>
          </ResponsiveContainer>
        </div>
        <div className="h-36">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={b}><XAxis dataKey="x" fontSize={10} /><YAxis hide /><Tooltip /><Bar dataKey="share" fill="#10b981" name="Monitored text" /></BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        <Quantiles name="Calibration negatives" p={calibration} />
        <Quantiles name="Monitored text" p={monitored} />
      </div>
      {check && <p className="mt-2 text-sm" data-testid="overlap-note">{check.figure ? `${check.figure}. ` : ''}{check.reason}</p>}
    </div>
  );
}

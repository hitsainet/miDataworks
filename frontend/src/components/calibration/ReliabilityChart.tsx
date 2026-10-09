// "When it says P, how often do people agree?" (FR-006.11): bars of the share people labeled
// positive per probability bin, with counts; sparse bins greyed; a table view for accessibility.
import { useState } from 'react';
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { ReliabilityBin } from '@/types/calibration';

import { fmtInt, fmtPct } from './format';

export function ReliabilityChart({ bins, reason }: { bins: ReliabilityBin[] | null; reason?: string }) {
  const [table, setTable] = useState(false);
  if (!bins) return <p className="text-sm text-slate-500 dark:text-slate-400">Not available: {reason ?? 'no probabilities'}.</p>;
  const data = bins.map((b) => ({ name: `${b.lo.toFixed(1)}–${b.hi.toFixed(1)}`, share: b.positive_fraction ?? 0, n: b.n, sparse: b.sparse }));
  return (
    <div data-testid="reliability">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-sm font-medium">When it says P, how often do people agree?</h4>
        <button type="button" className="text-xs underline rounded focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => setTable((t) => !t)}>
          {table ? 'Show chart' : 'Show as table'}
        </button>
      </div>
      {table ? (
        <table className="w-full text-xs tabular-nums" data-testid="reliability-table">
          <thead><tr className="text-left text-slate-500"><th>Probability</th><th>Rows</th><th>People said positive</th></tr></thead>
          <tbody>
            {bins.map((b) => (
              <tr key={b.lo} className={b.sparse ? 'text-slate-400' : ''}>
                <td>{b.lo.toFixed(1)}–{b.hi.toFixed(1)}</td>
                <td>{fmtInt(b.n)}{b.sparse ? ' (under 30 rows)' : ''}</td>
                <td>{b.positive_fraction === null ? '—' : fmtPct(b.positive_fraction)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <XAxis dataKey="name" tick={{ fontSize: 10 }} />
              <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v, _n, p) => [`${typeof v === 'number' ? fmtPct(v) : '—'} of ${fmtInt((p.payload as { n: number }).n)} rows`, 'People said positive']} />
              <Bar dataKey="share">
                {data.map((d) => <Cell key={d.name} fill={d.sparse ? '#94a3b8' : '#6366f1'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

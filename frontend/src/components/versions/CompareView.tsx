// Compare (FR-002.38, FR-002.43): both versions in columns, charts on SHARED bins, and every
// caption names its sample size ("on 10,914 rows").
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { CompareReport, Histogram, TopValues } from '@/types/versions';
import { formatCount } from '@/utils/format';

function histogramRows(d: Histogram) {
  return d.a.map((a, i) => ({ bin: `${d.bins[i].toFixed(d.kind === 'length' ? 0 : 2)}`, a, b: d.b[i] }));
}

function categoricalRows(d: TopValues) {
  return d.values.map((v) => ({ bin: v, a: d.a[v] ?? 0, b: d.b[v] ?? 0 }));
}

export function CompareView({ report }: { report: CompareReport }) {
  const a = `v${report.version_a.number}`;
  const b = `v${report.version_b.number}`;
  return (
    <section className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5 mb-5" aria-labelledby="compare-title" data-testid="compare-view">
      <h2 id="compare-title" className="font-semibold mb-3">Compare {a} with {b}</h2>
      <div className="grid gap-5 lg:grid-cols-2 mb-4 text-xs">
        <table className="w-full tabular-nums">
          <thead><tr className="text-slate-500 dark:text-slate-400 text-left"><th className="pb-1 font-medium">Split</th><th className="pb-1 font-medium text-right">{a}</th><th className="pb-1 font-medium text-right">{b}</th><th className="pb-1 font-medium text-right">Difference</th></tr></thead>
          <tbody>
            {report.splits.map((s) => (
              <tr key={s.split} className="border-t border-slate-200 dark:border-slate-700">
                <td className="py-1">{s.split}</td><td className="text-right">{s.rows_a.toLocaleString('en-US')}</td><td className="text-right">{s.rows_b.toLocaleString('en-US')}</td><td className="text-right">{s.difference > 0 ? '+' : ''}{s.difference.toLocaleString('en-US')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <dl className="grid grid-cols-2 gap-y-1">
          <dt className="text-slate-500 dark:text-slate-400">Rows added</dt><dd className="tabular-nums">{formatCount(report.keys.added, 'key')}</dd>
          <dt className="text-slate-500 dark:text-slate-400">Rows removed</dt><dd className="tabular-nums">{formatCount(report.keys.removed, 'key')}</dd>
          <dt className="text-slate-500 dark:text-slate-400">Rows changed</dt><dd className="tabular-nums">{formatCount(report.keys.changed, 'key')}</dd>
          <dt className="text-slate-500 dark:text-slate-400">Rows kept</dt><dd className="tabular-nums">{formatCount(report.keys.kept, 'key')}</dd>
        </dl>
      </div>
      {report.drop_log.length > 0 && (
        <table className="w-full text-xs tabular-nums mb-4">
          <thead><tr className="text-slate-500 dark:text-slate-400 text-left"><th className="pb-1 font-medium">Operator · reason</th><th className="pb-1 font-medium text-right">{a}</th><th className="pb-1 font-medium text-right">{b}</th></tr></thead>
          <tbody>
            {report.drop_log.map((d) => (
              <tr key={`${d.operator}-${d.reason_code}`} className="border-t border-slate-200 dark:border-slate-700"><td className="py-1">{d.operator} · {d.reason_code}</td><td className="text-right">{d.a.toLocaleString('en-US')}</td><td className="text-right">{d.b.toLocaleString('en-US')}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        {report.distributions.map((d) => {
          const data = d.kind === 'categorical' ? categoricalRows(d) : histogramRows(d);
          const what = d.kind === 'length' ? `Characters in ${d.column}` : d.kind === 'numeric' ? d.column : `Top values of ${d.column}`;
          return (
            <figure key={`${d.column}-${d.kind}`} className="min-w-0">
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.3} />
                    <XAxis dataKey="bin" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    {/* Recharts 3 sorts tooltip and legend items by name/value by default; sorting by
                        dataKey keeps a (this version) before b, the table's column order. */}
                    <Tooltip itemSorter="dataKey" />
                    <Legend itemSorter="dataKey" />
                    <Bar dataKey="a" name={a} fill="#6366f1" />
                    <Bar dataKey="b" name={b} fill="#94a3b8" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <figcaption className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                {what}, {a} on {formatCount(d.n_a, 'row')}, {b} on {formatCount(d.n_b, 'row')}
              </figcaption>
            </figure>
          );
        })}
      </div>
    </section>
  );
}

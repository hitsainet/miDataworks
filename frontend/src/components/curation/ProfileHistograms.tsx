// Profile figures (FR-004.6–004.8): histograms with named axes, and "Not computed — <reason>" blocks
// for every figure that could not be computed. A missing figure is never drawn as zero.
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { Figure, Histogram, Profile } from '@/types/curation';

import { count, pretty } from './format';

export function NotComputed({ name, figure }: { name: string; figure: Extract<Figure, { status: 'not_computed' }> }) {
  return (
    <div className="rounded border border-dashed border-slate-300 dark:border-slate-600 p-2 text-xs">
      <strong>{pretty(name)}: not computed</strong> — {figure.reason} {figure.action}
    </div>
  );
}

function Chart({ title, histogram }: { title: string; histogram: Histogram }) {
  const data = histogram.counts.map((c, i) => ({ bin: Math.round(histogram.edges[i]), rows: c }));
  return (
    <figure className="text-xs">
      <figcaption className="mb-1">{title}</figcaption>
      <div style={{ width: '100%', height: 120 }}>
        <ResponsiveContainer>
          <BarChart data={data}>
            <XAxis dataKey="bin" label={{ value: histogram.unit, position: 'insideBottom', offset: -2 }} tick={{ fontSize: 10 }} />
            <YAxis label={{ value: 'rows', angle: -90, position: 'insideLeft' }} tick={{ fontSize: 10 }} />
            <Tooltip />
            <Bar dataKey="rows" fill="#6366f1" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export function ProfileHistograms({ profile }: { profile: Profile }) {
  const figures = profile.figures;
  const lengths = figures.lengths?.status === 'computed' ? (figures.lengths.columns as Record<string, Record<string, Histogram>>) : {};
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {count(profile.n_rows)} rows{profile.sample ? ' (a sample: every figure is an estimate)' : ''}.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {Object.entries(lengths).flatMap(([column, h]) => [
          <Chart key={`${column}-c`} title={`${column}: length in characters`} histogram={h.characters} />,
          <Chart key={`${column}-w`} title={`${column}: length in words`} histogram={h.words} />,
        ])}
      </div>
      <ul className="text-xs space-y-1">
        {Object.entries(figures).map(([name, figure]) =>
          figure.status === 'not_computed' ? (
            <li key={name}>
              <NotComputed name={name} figure={figure} />
            </li>
          ) : name === 'exact_duplicates' || name === 'near_duplicates' ? (
            <li key={name}>
              {pretty(name)}: {String(figure.groups)} groups, {String(figure.rows)} extra rows, on {count(figure.n_rows)} rows
              {name === 'near_duplicates' ? ` (basis ${String(figure.basis)})` : ''}.
            </li>
          ) : null,
        )}
      </ul>
    </div>
  );
}

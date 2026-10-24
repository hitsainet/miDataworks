// The probability histogram with both thresholds drawn on its scale, with text labels — never
// colour alone (FPRD 005 sections 4.2, 4.4). 20 bins of 0.05.
import { Bar, BarChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { tokens } from '@/config/brand';

export function bins(probabilities: number[], count = 20): Array<{ bin: number; label: string; rows: number }> {
  const out = Array.from({ length: count }, (_, i) => ({ bin: i / count, label: (i / count).toFixed(2), rows: 0 }));
  for (const p of probabilities) out[Math.min(count - 1, Math.floor(p * count))].rows += 1;
  return out;
}

export function ProbabilityHistogram({
  probabilities,
  positive,
  negative,
}: {
  probabilities: number[];
  positive: number | null;
  negative: number | null;
}) {
  const data = bins(probabilities);
  const summary = `Histogram of ${probabilities.length} probabilities; positive at or above ${positive ?? '—'}, negative at or below ${negative ?? '—'}.`;
  return (
    <figure data-testid="probability-histogram" aria-label={summary} className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={3} />
          <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
          <Tooltip />
          <Bar dataKey="rows" fill={tokens.accentFill.dark} isAnimationActive={false} />
          {negative !== null && (
            <ReferenceLine x={(Math.floor(negative * 20) / 20).toFixed(2)} stroke={tokens.amber.dark} label={{ value: `negative ≤ ${negative}`, fontSize: 10, position: 'top' }} />
          )}
          {positive !== null && (
            <ReferenceLine x={(Math.floor(positive * 20) / 20).toFixed(2)} stroke={tokens.green.dark} label={{ value: `positive ≥ ${positive}`, fontSize: 10, position: 'top' }} />
          )}
        </BarChart>
      </ResponsiveContainer>
      <figcaption className="sr-only">{summary}</figcaption>
    </figure>
  );
}

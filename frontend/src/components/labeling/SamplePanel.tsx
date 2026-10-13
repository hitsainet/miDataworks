// "Try it on a sample" results (FR-005.20): each row's probability and the label the thresholds
// give. Never written as labels.
import type { SampleResult } from '@/types/labeling';

import { prob } from './format';

export function SamplePanel({ sample }: { sample: SampleResult | null }) {
  if (!sample) return null;
  return (
    <div data-testid="sample-panel" className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-900">
        {sample.rows.length} sampled rows from {sample.model} · {sample.steering_state} · not saved
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-500 dark:text-slate-400">
            <th className="px-3 py-1.5 font-medium">Row</th>
            <th className="px-3 py-1.5 font-medium">Probability</th>
            <th className="px-3 py-1.5 font-medium">Label</th>
          </tr>
        </thead>
        <tbody>
          {sample.rows.map((row) => (
            <tr key={row.row_key} className="border-t border-slate-100 dark:border-slate-800 align-top">
              <td className="px-3 py-1.5 max-w-md truncate" title={row.text}>{row.text}</td>
              <td className="px-3 py-1.5 font-mono whitespace-nowrap">{row.verdict ?? prob(row.probability)}</td>
              <td className="px-3 py-1.5">{row.error ? <span className="text-red-600 dark:text-red-400">{row.error}</span> : row.outcome ?? 'set both thresholds'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

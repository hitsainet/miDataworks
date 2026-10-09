// One figure with its scale and sample (copy rule: every number names its scale and sample), or
// "Not available" with the reason — never a zero standing in for a missing figure.
import type { ReactNode } from 'react';

export function StatTile({ label, value, sample, unavailable, struck, testId }: {
  label: string;
  value?: ReactNode;
  sample?: ReactNode;
  unavailable?: string | null;
  struck?: string | null;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className="rounded-lg border border-slate-200 dark:border-slate-700/60 bg-slate-50 dark:bg-slate-800/40 p-3 min-w-0">
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      {unavailable ? (
        <div className="mt-1 text-sm text-slate-500 dark:text-slate-400">{unavailable}</div>
      ) : (
        <>
          <div className={`mt-1 text-xl font-semibold tabular-nums ${struck ? 'line-through text-slate-400' : 'text-slate-900 dark:text-slate-100'}`}>{value}</div>
          {sample && <div className="mt-0.5 text-xs tabular-nums text-slate-500 dark:text-slate-400">{sample}</div>}
          {struck && <div className="mt-1 text-xs text-red-700 dark:text-red-300">Does not measure what it claims: {struck}</div>}
        </>
      )}
    </div>
  );
}

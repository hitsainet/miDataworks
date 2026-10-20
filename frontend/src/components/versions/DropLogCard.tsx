// "Why rows left this version" (FR-002.27): counts by step, operator and reason, in step order,
// with the running row count. Event kinds are written as words, never colour alone.
import type { DropStep } from '@/types/versions';

export function DropLogCard({ steps }: { steps: DropStep[] }) {
  return (
    <section className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5" aria-labelledby="drop-log-title">
      <h2 id="drop-log-title" className="font-semibold mb-3">Why rows left this version</h2>
      {steps.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">This recipe has no steps that change rows.</p>
      ) : (
        <ol className="space-y-3 text-xs">
          {steps.map((s) => (
            <li key={s.step_index} data-testid="drop-step">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-slate-500">step {s.step_index}</span>
                <span className="font-medium">{s.operator} {s.operator_version}</span>
                {s.reused && <span className="text-slate-500 dark:text-slate-400">reused from an earlier build</span>}
                <span className="ml-auto tabular-nums text-slate-500 dark:text-slate-400">
                  {s.rows_in.toLocaleString('en-US')} in → {s.rows_out.toLocaleString('en-US')} out
                </span>
              </div>
              <ul className="mt-1 font-mono space-y-0.5">
                {s.reasons.map((r) => (
                  <li key={`${r.kind}-${r.reason_code}`} className="flex gap-3">
                    <span className="w-16 text-right tabular-nums">{r.count.toLocaleString('en-US')}</span>
                    <span>{r.kind} · {r.reason_code}{r.example ? ` · ${r.example}` : ''}</span>
                  </li>
                ))}
                {s.split_assigned > 0 && (
                  <li className="flex gap-3 text-indigo-700 dark:text-indigo-300">
                    <span className="w-16 text-right tabular-nums">{s.split_assigned.toLocaleString('en-US')}</span>
                    <span>split_assigned · rows assigned to a split</span>
                  </li>
                )}
                {s.reasons.length === 0 && s.split_assigned === 0 && (
                  <li className="flex gap-3 text-green-700 dark:text-green-400"><span className="w-16 text-right">0</span><span>no rows dropped or changed</span></li>
                )}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

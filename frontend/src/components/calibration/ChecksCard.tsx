// "Checks that guard the gate" (FR-006.15 – FR-006.17): one line per check, failed checks first,
// each naming its metric and rule; a failed metric "does not measure what it claims".
import type { CheckOut } from '@/types/calibration';

const RESULT_TEXT: Record<CheckOut['result'], string> = { pass: 'Pass', fail: 'Fail', not_applicable: 'Not applicable' };
const RESULT_TONE: Record<CheckOut['result'], string> = {
  pass: 'text-green-700 dark:text-green-300',
  fail: 'text-red-700 dark:text-red-300',
  not_applicable: 'text-slate-500 dark:text-slate-400',
};

export function ChecksCard({ checks, conformance }: { checks: CheckOut[]; conformance: Record<string, unknown> | null }) {
  const order = { fail: 0, not_applicable: 1, pass: 2 } as const;
  const sorted = [...checks].sort((a, b) => order[a.result] - order[b.result]);
  return (
    <div data-testid="checks-card">
      <h4 className="mb-2 text-sm font-medium">Checks that guard the gate</h4>
      <ul className="space-y-1 text-xs">
        {sorted.map((c) => (
          <li key={`${c.metric_id}-${c.check_id}`} data-testid="check-line" data-result={c.result}>
            <span className={`font-medium ${RESULT_TONE[c.result]}`}>{RESULT_TEXT[c.result]}</span>{' '}
            <span className="font-mono">{c.check_id}</span> on <span className="font-mono">{c.metric_id}</span>
            {c.result === 'fail' && <span>: does not measure what it claims</span>}
            {c.reason && <span className="text-slate-500 dark:text-slate-400"> — {c.reason}</span>}
          </li>
        ))}
      </ul>
      {conformance && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-300" data-testid="conformance">
          Implementation conformance recorded on the template: {JSON.stringify(conformance)}
        </p>
      )}
    </div>
  );
}

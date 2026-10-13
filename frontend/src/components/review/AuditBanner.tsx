// The audit of a version (FR-006.27 – FR-006.29): progress toward completion. Only operator
// decisions count; completion alone satisfies the export rule (P-02).
import type { AuditStatus } from '@/types/review';

export function AuditBanner({ status }: { status: AuditStatus }) {
  if (status.state === 'none') return null;
  const done = status.state === 'complete';
  return (
    <div role="status" data-testid="audit-banner" data-state={status.state}
      className={`rounded-lg border px-3 py-2 text-sm ${done ? 'border-green-300 dark:border-green-500/40 bg-green-50 dark:bg-green-500/10' : 'border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10'}`}>
      {done && status.result
        ? `Audit complete: ${status.result.accept} accepted, ${status.result.override} overridden, ${status.result.flag} flagged of ${status.result.size} rows (agreement ${((status.result.agreement_share ?? 0) * 100).toFixed(1)}%).`
        : `Audit in progress: ${status.decided} of ${status.size} rows decided by the operator. Agent decisions do not count.`}
    </div>
  );
}

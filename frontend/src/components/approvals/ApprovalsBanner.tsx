// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/steering/ApprovalsBanner.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: renders nothing when nothing is pending; a slow poll; approve
// and reject per request. Changed: reads dw_approvals through approvalsStore (the REST gate,
// ADR-013); shows what the agent asked, who asked and when it expires; buttons name their action.
import { useEffect } from 'react';
import { Bot } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { useApprovalsStore } from '@/stores/approvalsStore';

export const APPROVALS_POLL_MS = 15000;

export function ApprovalsBanner() {
  const pending = useApprovalsStore((s) => s.pending);
  const error = useApprovalsStore((s) => s.error);
  const fetchPending = useApprovalsStore((s) => s.fetchPending);
  const approve = useApprovalsStore((s) => s.approve);
  const reject = useApprovalsStore((s) => s.reject);

  useEffect(() => {
    void fetchPending();
    const timer = setInterval(() => void fetchPending(), APPROVALS_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchPending]);

  if (pending.length === 0) return null;
  return (
    <div className="mb-6 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4" data-testid="approvals-banner">
      <div className="flex items-center gap-2 mb-2 text-sm font-medium text-amber-800 dark:text-amber-300">
        <Bot size={16} aria-hidden="true" />
        {pending.length} agent request{pending.length > 1 ? 's' : ''} waiting for your approval
      </div>
      {error && <p className="text-xs text-red-700 dark:text-red-400 mb-2">{error}</p>}
      <ul className="space-y-2">
        {pending.map((request) => (
          <li key={request.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-white dark:bg-slate-900 px-3 py-2">
            <div className="flex-1 min-w-0 text-xs text-slate-700 dark:text-slate-300">
              <span className="font-mono">{request.action}</span> · {request.summary} · asked by{' '}
              <span className="font-mono">{request.requested_by}</span> · expires {new Date(request.expires_at).toLocaleString()}
            </div>
            <Button size="sm" onClick={() => void approve(request.id)}>Approve this request</Button>
            <Button variant="secondary" size="sm" onClick={() => void reject(request.id, 'Rejected by the operator.')}>
              Reject this request
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

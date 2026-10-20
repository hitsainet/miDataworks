// Earlier agent requests (archive, operator decision 2026-10-20): every decided approval, newest
// first, with Archive and Unarchive on the rejected and expired ones. Archiving hides a request from
// this list; it decides nothing and deletes nothing. Pending requests live in the banner.
import { useEffect } from 'react';

import { ArchiveButton, ArchivedNote, ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { ACTION_LABELS } from '@/components/settings/AgentAccessCard';
import { useApprovalsStore } from '@/stores/approvalsStore';
import type { Approval } from '@/types/api';
import { formatEt } from '@/utils/format';

const STATUS_LABEL: Record<Approval['status'], string> = {
  pending: 'Waiting',
  executing: 'Running',
  executed: 'Approved and run',
  failed: 'Approved, then failed',
  rejected: 'Rejected',
  expired: 'Expired',
};

/** Only these may be archived (the backend refuses the rest: approval_not_archivable). */
export const ARCHIVABLE_STATUSES: ReadonlySet<Approval['status']> = new Set(['rejected', 'expired']);

const card = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';

export function ApprovalHistoryCard() {
  const history = useApprovalsStore((s) => s.history);
  const includeArchived = useApprovalsStore((s) => s.historyIncludeArchived);
  const error = useApprovalsStore((s) => s.historyError);
  const fetchHistory = useApprovalsStore((s) => s.fetchHistory);
  const setHistoryIncludeArchived = useApprovalsStore((s) => s.setHistoryIncludeArchived);
  const archiveApproval = useApprovalsStore((s) => s.archiveApproval);
  const unarchiveApproval = useApprovalsStore((s) => s.unarchiveApproval);

  useEffect(() => {
    void fetchHistory();
  }, [fetchHistory]);

  return (
    <section className={card} aria-labelledby="approval-history-title" data-testid="approval-history">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <h2 id="approval-history-title" className="font-semibold text-slate-900 dark:text-slate-100">Earlier agent requests</h2>
        <ShowArchivedToggle checked={includeArchived} onChange={(v) => void setHistoryIncludeArchived(v)} testId="show-archived-approvals" />
      </div>
      <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
        Requests you already decided, or that expired. Archive a rejected or expired one to hide it here; nothing is deleted.
      </p>
      {error && <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {history.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">{includeArchived ? 'No decided requests, archived or not.' : 'No decided requests.'}</p>
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-700 max-h-96 overflow-y-auto" data-testid="approval-history-list">
          {history.map((a) => (
            <li key={a.id} className="py-2 text-sm" data-testid="approval-history-item">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium">{ACTION_LABELS[a.action] ?? a.action}</span>
                  <span className="text-slate-500 dark:text-slate-400"> · {STATUS_LABEL[a.status] ?? a.status} · asked by {a.requested_by}</span>
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    Asked {formatEt(a.created_at) ?? 'at an unrecorded time'}
                    {a.decided_by ? ` · decided by ${a.decided_by}` : ''}
                    {a.decided_at ? ` ${formatEt(a.decided_at) ?? ''}` : ''}
                    {a.reason ? ` · ${a.reason}` : ''}
                  </div>
                  <ArchivedNote item={a} />
                </div>
                {ARCHIVABLE_STATUSES.has(a.status) && (
                  <ArchiveButton item={a} noun="request" onArchive={() => archiveApproval(a.id)} onUnarchive={() => unarchiveApproval(a.id)} />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

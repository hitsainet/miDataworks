// History (ENH-008 D6, section 2.2): every decided request, the most recent decision first, 200 at a
// time with Show older, in the shared columns: Outcome, Action, Asked by, Asked, Decided by,
// Decided, Reason, Result, Id. On a narrow screen each row stacks into a card in the same order
// (one table whose rows and cells become blocks, so nothing is drawn twice).
//
// Archive (operator decision 2026-10-20) is miDataworks's extra: Archive and Unarchive on the
// rejected and expired requests hide them from this list; archiving decides nothing and deletes
// nothing. Every value from a request is a text node (section 2.7).
import { useEffect } from 'react';
import type { ReactNode } from 'react';

import { AskedBy, actionLabel, asText } from '@/components/approvals/ApprovalCard';
import { ArchiveButton, ArchivedNote, ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { Button } from '@/components/common/Button';
import { LocalTime } from '@/components/common/LocalTime';
import { PurgeBar, PurgeBlockerNote, PurgeItemCheckbox, usePurgeSelection } from '@/components/common/PurgeControls';
import { useApprovalsStore } from '@/stores/approvalsStore';
import type { Approval } from '@/types/api';

/** The shared outcome vocabulary (section 2.2). */
export const STATUS_LABEL: Record<Approval['status'], string> = {
  pending: 'Waiting',
  executing: 'Running',
  executed: 'Approved and run',
  failed: 'Approved, then failed',
  rejected: 'Rejected',
  expired: 'Expired',
};

export const HISTORY_COLUMNS = ['Outcome', 'Action', 'Asked by', 'Asked', 'Decided by', 'Decided', 'Reason', 'Result', 'Id'] as const;

/** Only these may be archived (the backend refuses the rest: approval_not_archivable). */
export const ARCHIVABLE_STATUSES: ReadonlySet<Approval['status']> = new Set(['rejected', 'expired']);

const card = 'rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';

const PILL: Record<Approval['status'], string> = {
  pending: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
  executing: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
  executed: 'bg-green-500/15 text-green-800 dark:text-green-300',
  failed: 'bg-red-500/15 text-red-800 dark:text-red-300',
  rejected: 'bg-slate-500/15 text-slate-800 dark:text-slate-200',
  expired: 'bg-slate-500/15 text-slate-600 dark:text-slate-300',
};

/** Who decided: the recorded name, "operator" when none was recorded, "nobody (expired)". */
export function decidedBy(a: Approval): string {
  if (a.status === 'expired') return 'nobody (expired)';
  return a.decided_by || 'operator';
}

/** What the request produced: the resource, the error, an expiry, or a dash. */
export function resultText(a: Approval): string {
  if (a.status === 'expired') return 'expired without a decision';
  if (a.error) {
    const { code, message } = a.error as { code?: unknown; message?: unknown };
    const parts = [code, message].filter((p) => p !== undefined && p !== null).map(asText);
    return parts.length > 0 ? parts.join(': ') : asText(a.error);
  }
  if (a.result_id) return a.result_kind ? `${a.result_kind} ${a.result_id}` : a.result_id;
  return '—';
}

function Cell({ label, children, className = '' }: { label: (typeof HISTORY_COLUMNS)[number]; children: ReactNode; className?: string }) {
  return (
    <td className={`block py-0.5 md:table-cell md:py-2 md:pr-3 align-top ${className}`} data-column={label}>
      <span className="mr-1 font-medium text-slate-500 md:hidden dark:text-slate-400">{label}:</span>
      {children}
    </td>
  );
}

export function ApprovalHistoryCard() {
  const history = useApprovalsStore((s) => s.history);
  const hasOlder = useApprovalsStore((s) => s.historyHasOlder);
  const includeArchived = useApprovalsStore((s) => s.historyIncludeArchived);
  const error = useApprovalsStore((s) => s.historyError);
  const fetchHistory = useApprovalsStore((s) => s.fetchHistory);
  const fetchOlderHistory = useApprovalsStore((s) => s.fetchOlderHistory);
  const setHistoryIncludeArchived = useApprovalsStore((s) => s.setHistoryIncludeArchived);
  const archiveApproval = useApprovalsStore((s) => s.archiveApproval);
  const unarchiveApproval = useApprovalsStore((s) => s.unarchiveApproval);

  useEffect(() => {
    void fetchHistory();
  }, [fetchHistory]);
  const purge = usePurgeSelection(
    'approval',
    history.map((a) => ({ id: a.id, name: `request ${actionLabel(a.action)} (${a.id})`, archived: Boolean(a.archived_at) })),
    includeArchived,
  );

  return (
    <section className={card} aria-labelledby="approval-history-title" data-testid="approval-history">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <h2 id="approval-history-title" className="font-semibold text-slate-900 dark:text-slate-100">History</h2>
        <ShowArchivedToggle checked={includeArchived} onChange={(v) => void setHistoryIncludeArchived(v)} testId="show-archived-approvals" />
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">Requests you decided, and the ones that expired.</p>
      <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
        Archive a rejected or expired one to hide it here; nothing is deleted.
      </p>
      {error && <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {includeArchived && <div className="mb-3"><PurgeBar selection={purge} onPurged={() => fetchHistory()} /></div>}
      {history.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="approval-history-empty">
          {includeArchived ? 'No decided requests yet, archived or not.' : 'No decided requests yet.'}
        </p>
      ) : (
        <div className="max-h-[32rem] overflow-y-auto">
          <table className="block w-full text-left text-xs md:table" data-testid="approval-history-list">
            <thead className="hidden md:table-header-group">
              <tr className="text-slate-500 dark:text-slate-400">
                {HISTORY_COLUMNS.map((c) => (
                  <th key={c} scope="col" className="py-1 pr-3 font-medium">{c}</th>
                ))}
                <th scope="col" className="py-1"><span className="sr-only">Archive</span></th>
              </tr>
            </thead>
            <tbody className="block divide-y divide-slate-200 md:table-row-group dark:divide-slate-700">
              {history.map((a) => (
                <tr key={a.id} className="block py-2 md:table-row md:py-0" data-testid="approval-history-item">
                  <Cell label="Outcome">
                    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${PILL[a.status] ?? ''}`} data-testid="approval-outcome">
                      {STATUS_LABEL[a.status] ?? a.status}
                    </span>
                  </Cell>
                  <Cell label="Action">
                    <span className="font-medium text-slate-900 dark:text-slate-100">{actionLabel(a.action)}</span>
                    {a.summary && <div className="whitespace-pre-wrap break-words text-slate-600 dark:text-slate-400">{a.summary}</div>}
                    <ArchivedNote item={a} />
                    <PurgeBlockerNote selection={purge} id={a.id} />
                  </Cell>
                  <Cell label="Asked by"><AskedBy who={a.requested_by} /></Cell>
                  <Cell label="Asked"><LocalTime iso={a.created_at} /></Cell>
                  <Cell label="Decided by">{decidedBy(a)}</Cell>
                  <Cell label="Decided"><LocalTime iso={a.status === 'expired' ? (a.decided_at ?? a.expires_at) : a.decided_at} /></Cell>
                  <Cell label="Reason" className="break-words">{a.reason ? a.reason : '—'}</Cell>
                  <Cell label="Result" className="break-words"><span className="font-mono" data-testid="approval-result">{resultText(a)}</span></Cell>
                  <Cell label="Id"><code className="font-mono text-[11px]">{a.id}</code></Cell>
                  <td className="block py-0.5 md:table-cell md:py-2 align-top">
                    <div className="flex items-center gap-2">
                      <PurgeItemCheckbox selection={purge} id={a.id} name={`request ${a.id}`} />
                      {ARCHIVABLE_STATUSES.has(a.status) && (
                        <ArchiveButton item={a} noun="request" onArchive={() => archiveApproval(a.id)} onUnarchive={() => unarchiveApproval(a.id)} />
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {hasOlder && (
        <div className="mt-3">
          <Button size="sm" variant="secondary" onClick={() => void fetchOlderHistory()} data-testid="approval-history-older">
            Show older
          </Button>
        </div>
      )}
    </section>
  );
}

// The Approvals screen (operator, 2026-10-20: "where do I approve an agent request?"; ENH-008 D6).
// "Waiting for you (n)": the requests that wait, the soonest to expire first, as the same cards as
// the banner (ApprovalCard), then the History (ApprovalHistoryCard). It does not poll: the banner, mounted on every screen, owns the
// one poll of the pending list, and this screen reads the same store. A decision refreshes the
// decided list, so an approved request moves from the first list to the second.
import { useEffect, useRef, useState } from 'react';

import { ApprovalCardList } from '@/components/approvals/ApprovalCard';
import { ApproveAllButtons } from '@/components/approvals/ApproveAll';
import { APPROVALS_POLL_MS } from '@/components/approvals/ApprovalsBanner';
import { ApprovalHistoryCard } from '@/components/approvals/ApprovalHistoryCard';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useApprovalsStore } from '@/stores/approvalsStore';

const card = 'rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';

export function ApprovalsPanel({ panel }: { panel: PanelDef }) {
  const pending = useApprovalsStore((s) => s.pending);
  const decisionError = useApprovalsStore((s) => s.error);
  const errorId = useApprovalsStore((s) => s.errorId);
  const loadError = useApprovalsStore((s) => s.loadError);
  const fetchHistory = useApprovalsStore((s) => s.fetchHistory);
  // A decision error whose card is still listed is drawn inside that card.
  const strayDecisionError = decisionError && !pending.some((a) => a.id === errorId) ? decisionError : null;
  const error = strayDecisionError ?? (loadError ? `Could not read the approval queue: ${loadError}` : null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), APPROVALS_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  // The decided list changes when the pending list does (a decision, an expiry). The history card
  // loads itself on mount, so the first render is skipped here.
  const pendingKey = pending.map((a) => a.id).join(',');
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void fetchHistory();
  }, [pendingKey, fetchHistory]);

  return (
    <>
      <PageHead help="approvals" title={panel.title} subtitle={panel.subtitle} />
      <div className="stack-y-5">
        <section className={card} aria-labelledby="approvals-pending-title" data-testid="approvals-pending">
          <h2 id="approvals-pending-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">
            Waiting for you ({pending.length})
          </h2>
          <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
            An AI agent asked to do each of these. Nothing happens until you approve it; a request you do not decide expires.
          </p>
          {error && (
            <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">
              {error}
            </p>
          )}
          <ApproveAllButtons pending={pending} />
          {pending.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="approvals-empty">
              No agent requests are waiting.
            </p>
          ) : (
            <ApprovalCardList requests={pending} now={now} className="stack-y-2 rounded-card bg-amber-500/10 p-2" />
          )}
        </section>
        <ApprovalHistoryCard />
      </div>
    </>
  );
}

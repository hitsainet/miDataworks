// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/steering/ApprovalsBanner.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: renders nothing when nothing is pending; a slow poll; approve
// and reject per request. Changed: reads dw_approvals through approvalsStore (the REST gate,
// ADR-013); the cards are ApprovalCard (ENH-008 section 2.1), shared with the Approvals screen.
//
// The banner (ENH-008 D5, section 2.3) is how the operator NOTICES a request on every screen: a
// labelled region with the count, Open Approvals, at most 3 cards (the soonest to expire), "and N
// more waiting", and Collapse/Expand kept for the browser session (a new request expands it again).
// It owns the one poll of the pending list (the sidebar badge and the Approvals screen read the same
// store), and on the Approvals screen keeps polling but draws nothing, so no request is listed
// twice. A failed load is said, never silent. Decisions go through approvalsStore -> api client,
// which asks for the Settings PIN when the server refuses an approval without it (feature 014).
import { useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';

import { ApprovalCardList } from '@/components/approvals/ApprovalCard';
import { ApproveAllButtons } from '@/components/approvals/ApproveAll';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { navigate } from '@/utils/navigate';

export { expiresIn, ApprovalCard as RequestRow } from '@/components/approvals/ApprovalCard';

export const APPROVALS_POLL_MS = 15000;
/** The banner shows at most this many cards; the rest are on the Approvals screen. */
export const BANNER_MAX_CARDS = 3;
/** Sticky at the top of the scrolling window; above page content, under the sidebar and drawers. */
export const BANNER_DOCK_CLASS = 'sticky top-14 z-20 mb-6 bg-slate-50 dark:bg-slate-950';
export const BANNER_COLLAPSED_KEY = 'midataworks.approvals-banner.collapsed';

/** The requests that were waiting when the operator collapsed the banner, or null (expanded). The
 *  banner stays collapsed while every waiting request is one of them, so a new one expands it. */
function readCollapsed(): string[] | null {
  try {
    const raw = window.sessionStorage.getItem(BANNER_COLLAPSED_KEY);
    const ids: unknown = raw === null ? null : JSON.parse(raw);
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : null;
  } catch {
    return null;
  }
}

function writeCollapsed(ids: string[] | null): void {
  try {
    if (ids) window.sessionStorage.setItem(BANNER_COLLAPSED_KEY, JSON.stringify(ids));
    else window.sessionStorage.removeItem(BANNER_COLLAPSED_KEY);
  } catch {
    // No session storage (a private window): the choice lasts until the page reloads.
  }
}

const linkClass =
  'text-xs font-medium underline text-amber-900 dark:text-amber-200 hover:text-amber-700 dark:hover:text-amber-100';

export function ApprovalsBanner({ onApprovalsScreen = false }: { onApprovalsScreen?: boolean } = {}) {
  const pending = useApprovalsStore((s) => s.pending);
  const decisionError = useApprovalsStore((s) => s.error);
  const errorId = useApprovalsStore((s) => s.errorId);
  const loadError = useApprovalsStore((s) => s.loadError);
  const fetchPending = useApprovalsStore((s) => s.fetchPending);
  const [now, setNow] = useState(() => Date.now());
  const [collapsedIds, setCollapsedIds] = useState(readCollapsed);

  useEffect(() => {
    void fetchPending();
    const timer = setInterval(() => {
      setNow(Date.now());
      void fetchPending();
    }, APPROVALS_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchPending]);

  if (onApprovalsScreen || (pending.length === 0 && !loadError && !decisionError)) return null;
  const shown = pending.slice(0, BANNER_MAX_CARDS);
  const more = pending.length - shown.length;
  // A decision error whose card is still shown is drawn inside that card.
  // Collapsed while every waiting request was waiting when it was collapsed: a new one expands it.
  const collapsed = collapsedIds !== null && pending.every((a) => collapsedIds.includes(a.id));
  const strayDecisionError = decisionError && (collapsed || !shown.some((a) => a.id === errorId)) ? decisionError : null;
  const toggle = () => {
    const next = collapsed ? null : pending.map((a) => a.id);
    setCollapsedIds(next);
    writeCollapsed(next);
  };

  // The dock floats (operator 2026-10-28): sticky just under the header, which is itself sticky
  // and `h-14` tall (the window is the scrolling element), so neither the status bar nor the
  // waiting requests ever scroll out of sight. It is opaque
  // because the region's amber tint is translucent; z-20 keeps it above page content and
  // under the sidebar (z-30) and the drawers (z-40). The region caps its height and scrolls
  // inside, so it never hides the whole screen; collapsed it is one sticky line.
  return (
    <div className={BANNER_DOCK_CLASS} data-testid="approvals-banner-dock">
      <div
        role="region"
        aria-label="Agent requests waiting for your approval"
        className="max-h-[40vh] overflow-y-auto rounded-card border border-amber-500/40 bg-amber-500/10 p-4"
        data-testid="approvals-banner"
      >
        <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-amber-800 dark:text-amber-300">
          <ShieldCheck size={16} aria-hidden="true" />
          <h2 className="flex-1" aria-live="polite" data-testid="approvals-banner-heading">
            {pending.length} agent request{pending.length === 1 ? '' : 's'} waiting for your approval
          </h2>
          <button type="button" onClick={() => navigate('approvals')} className={linkClass} data-testid="approvals-banner-open">
            Open Approvals
          </button>
          {pending.length > 0 && (
            <button
              type="button"
              onClick={toggle}
              aria-expanded={!collapsed}
              className={linkClass}
              data-testid="approvals-banner-toggle"
            >
              {collapsed ? 'Expand' : 'Collapse'}
            </button>
          )}
        </div>
        {loadError && (
          <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-400">
            Could not read the approval queue: {loadError}
          </p>
        )}
        {strayDecisionError && (
          <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-400">
            {strayDecisionError}
          </p>
        )}
        {!collapsed && shown.length > 0 && (
          <>
            {/* ENH-006 D79/D81: over every waiting request of each kind, not only the 3 cards shown here. */}
            <ApproveAllButtons pending={pending} />
            <ApprovalCardList requests={shown} now={now} className="mt-2 stack-y-2" />
            {more > 0 && (
              <p className="mt-2 text-xs text-amber-900 dark:text-amber-200" data-testid="approvals-banner-more">
                and {more} more waiting —{' '}
                <button type="button" onClick={() => navigate('approvals')} className={linkClass}>
                  Open Approvals
                </button>
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

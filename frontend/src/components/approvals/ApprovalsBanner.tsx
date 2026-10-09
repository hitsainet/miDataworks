// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/steering/ApprovalsBanner.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: renders nothing when nothing is pending; a slow poll; approve
// and reject per request. Changed: reads dw_approvals through approvalsStore (the REST gate,
// ADR-013); per request (010 FTID section 6) the identity, the action, one consequence sentence,
// an expiry countdown and buttons that name their action; a source-annotation card shows the
// source, the current and proposed annotation and the push warning (S3-01, 19.7).
import { useEffect, useState } from 'react';
import { Bot } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { WhoBadge } from '@/components/common/WhoBadge';
import { ACTION_LABELS } from '@/components/settings/AgentAccessCard';
import { useApprovalsStore } from '@/stores/approvalsStore';
import type { Approval } from '@/types/api';

export const APPROVALS_POLL_MS = 15000;

export function expiresIn(expiresAt: string, now: number): string {
  const ms = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(ms)) return 'expiry unknown';
  if (ms <= 0) return 'expired';
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `expires in ${Math.max(minutes, 1)} min`;
  return `expires in ${Math.round(minutes / 60)} h`;
}

interface AnnotationFacts {
  display_name?: string;
  repo_id?: string | null;
  content_hash?: string | null;
  current?: { kind?: string; redistribution?: string | null } | null;
  proposed?: { kind?: string; redistribution?: string | null; reason?: string };
  warning?: string;
}

function describe(a?: { kind?: string; redistribution?: string | null } | null): string {
  if (!a) return 'none';
  return [a.kind, a.redistribution].filter(Boolean).join(': ');
}

function AnnotationCard({ facts }: { facts: AnnotationFacts }) {
  return (
    <div className="mt-1 text-xs text-slate-700 dark:text-slate-300" data-testid="annotation-card">
      <div>
        Source <span className="font-medium">{facts.display_name}</span>{' '}
        <span className="font-mono">{facts.repo_id ?? facts.content_hash ?? ''}</span>
      </div>
      <div>
        Current: {describe(facts.current)} → proposed: {describe(facts.proposed)}
        {facts.proposed?.reason ? ` (${facts.proposed.reason})` : ''}
      </div>
      {facts.warning && (
        <div className="mt-0.5 font-medium text-amber-800 dark:text-amber-300" role="note">
          {facts.warning}
        </div>
      )}
    </div>
  );
}

function RequestRow({ request, now }: { request: Approval; now: number }) {
  const approve = useApprovalsStore((s) => s.approve);
  const reject = useApprovalsStore((s) => s.reject);
  const label = ACTION_LABELS[request.action] ?? request.action;
  const facts = (request.payload as { facts?: AnnotationFacts }).facts;
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg bg-white px-3 py-2 dark:bg-slate-900" data-testid="approval-row">
      <div className="flex-1 min-w-0 text-xs text-slate-700 dark:text-slate-300">
        <div className="flex flex-wrap items-center gap-2">
          <WhoBadge who={request.requested_by} />
          <span className="font-medium">{label}</span>
          <span className="text-slate-500 dark:text-slate-400">{expiresIn(request.expires_at, now)}</span>
        </div>
        <div className="mt-0.5">{request.summary}</div>
        {request.action === 'source_annotate' && facts && <AnnotationCard facts={facts} />}
      </div>
      <Button size="sm" onClick={() => void approve(request.id)}>
        Approve {label.toLowerCase()}
      </Button>
      <Button variant="secondary" size="sm" onClick={() => void reject(request.id, 'Rejected by the operator.')}>
        Reject
      </Button>
    </li>
  );
}

export function ApprovalsBanner() {
  const pending = useApprovalsStore((s) => s.pending);
  const decisionError = useApprovalsStore((s) => s.error);
  const loadError = useApprovalsStore((s) => s.loadError);
  const error = decisionError ?? loadError;
  const fetchPending = useApprovalsStore((s) => s.fetchPending);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    void fetchPending();
    const timer = setInterval(() => {
      setNow(Date.now());
      void fetchPending();
    }, APPROVALS_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchPending]);

  if (pending.length === 0 && !error) return null;
  return (
    <div className="mb-6 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4" data-testid="approvals-banner">
      {pending.length > 0 && (
        <div className="flex items-center gap-2 mb-2 text-sm font-medium text-amber-800 dark:text-amber-300">
          <Bot size={16} aria-hidden="true" />
          {pending.length} agent request{pending.length > 1 ? 's' : ''} waiting for your approval
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-700 dark:text-red-400 mb-2">
          {error}
        </p>
      )}
      <ul className="space-y-2">
        {pending.map((request) => (
          <RequestRow key={request.id} request={request} now={now} />
        ))}
      </ul>
    </div>
  );
}

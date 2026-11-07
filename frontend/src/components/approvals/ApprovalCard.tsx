// One waiting agent request (ENH-008 section 2.1), the same card on the banner and the Approvals
// screen. Fields, in this order: the action and its Waiting pill; who asked; when it was asked and
// when it expires (the exact instant in each tooltip); the facts; the extra card a kind needs (a
// source annotation, a gate target, a code rule); every argument the agent sent, collapsed; the
// request id; Approve and Reject. Reject opens an optional reason (D8c). Every piece of text from a
// request is a React text node, never markup (section 2.7).
//
// The annotation and target cards came from ApprovalsBanner.tsx (origin miStudio
// frontend/src/components/steering/ApprovalsBanner.tsx @ c829a2cc, adapted; docs/REUSE.md):
// S3-01 / 19.7 and S3-08 / FR-006.43 / 010 FTASKS 19.7.
import { forwardRef, useEffect, useRef, useState } from 'react';
import { Copy } from 'lucide-react';

import { CodeRuleCard } from '@/components/approvals/CodeRuleCard';
import { PaidRunCard, type PaidRunFacts } from '@/components/behaviour/Behaviour017';
import { Button } from '@/components/common/Button';
import { LocalTime } from '@/components/common/LocalTime';
import { WhoBadge } from '@/components/common/WhoBadge';
import { ACTION_LABELS } from '@/components/settings/AgentAccessCard';
import { DEFAULT_REJECT_REASON, useApprovalsStore } from '@/stores/approvalsStore';
import type { Approval } from '@/types/api';
import type { CodeRuleFacts } from '@/types/codeRules';
import { isoInstant } from '@/utils/format';

export { DEFAULT_REJECT_REASON };

export const REJECT_REASON_MAX = 1000;

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function expiresIn(expiresAt: string | null | undefined, now: number): string {
  if (!expiresAt) return 'never expires';
  const ms = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(ms)) return 'expiry unknown';
  if (ms <= 0) return 'expired';
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `expires in ${Math.max(minutes, 1)} min`;
  return `expires in ${Math.round(minutes / 60)} h`;
}

/** Any value as text: a string as it is, anything else through JSON.stringify (still text). */
export function asText(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
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

interface VerdictChange {
  record_id: string;
  labeler?: { identity_hash?: string; fingerprint?: string | null; model_id?: string | null };
  version_ids?: string[];
  old_verdict?: string;
  new_verdict?: string;
}

interface TargetFacts {
  question?: string;
  old_display?: string;
  new?: number;
  verdict_changes?: VerdictChange[];
  verdict_changes_note?: string;
}

function labelerName(change: VerdictChange): string {
  const l = change.labeler ?? {};
  return l.model_id || (l.fingerprint ?? l.identity_hash ?? change.record_id).slice(0, 12);
}

function TargetCard({ facts }: { facts: TargetFacts }) {
  const changes = facts.verdict_changes ?? [];
  return (
    <div className="mt-1 text-xs text-slate-700 dark:text-slate-300" data-testid="target-card">
      <div>
        Question <span className="font-medium">{facts.question}</span>
      </div>
      <div>
        Gate target (AUROC lower bound): {facts.old_display ?? 'default (C3)'} → {typeof facts.new === 'number' ? facts.new.toFixed(3) : '?'}
      </div>
      {changes.length === 0 ? (
        <div className="mt-0.5" data-testid="verdict-changes-none">No verdict would change.</div>
      ) : (
        <>
          <div className="mt-0.5 font-medium text-amber-800 dark:text-amber-300" role="note">
            {changes.length} labeler verdict{changes.length > 1 ? 's' : ''} would change
          </div>
          <ul className="mt-0.5 list-disc pl-5" data-testid="verdict-changes">
            {changes.map((c) => (
              <li key={c.record_id}>
                <span className="font-mono">{labelerName(c)}</span>: {c.old_verdict} → {c.new_verdict}
                {c.version_ids && c.version_ids.length > 0
                  ? ` (version${c.version_ids.length > 1 ? 's' : ''} ${c.version_ids.join(', ')})`
                  : ''}
              </li>
            ))}
          </ul>
          {facts.verdict_changes_note && <div className="mt-0.5 text-slate-500 dark:text-slate-400">{facts.verdict_changes_note}</div>}
        </>
      )}
    </div>
  );
}

/** The source an agent sent with a code-rule save (payload.body.document.source), or undefined. */
function codeRuleSource(payload: Record<string, unknown>): string | undefined {
  const source = (payload as { body?: { document?: { source?: unknown } } }).body?.document?.source;
  return typeof source === 'string' ? source : undefined;
}

/** "asked by agent dataworks-mcp", or "asked by an agent" when no name was recorded. */
export function AskedBy({ who }: { who: string | null | undefined }) {
  if (!who) return <span>asked by an agent</span>;
  return (
    <span className="inline-flex items-center gap-1">
      asked by <WhoBadge who={who} />
    </span>
  );
}

/** 017 FR-017.40: what a paid classifier run will cost (payload.paid), or undefined. */
function paidFacts(payload: Record<string, unknown>): PaidRunFacts | undefined {
  const paid = (payload as { paid?: unknown }).paid;
  return paid && typeof paid === 'object' ? (paid as PaidRunFacts) : undefined;
}

/** Every key and value of the stored payload, collapsed (section 2.1 field 6). */
export function AllArguments({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload ?? {});
  return (
    <details className="mt-2 text-xs" data-testid="approval-arguments">
      <summary className="cursor-pointer text-slate-600 dark:text-slate-400">All arguments the agent sent</summary>
      {entries.length === 0 ? (
        <p className="mt-1 text-slate-500 dark:text-slate-400">None.</p>
      ) : (
        <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="font-mono text-slate-500 dark:text-slate-400">{key}</dt>
              <dd className="min-w-0">
                <pre className="whitespace-pre-wrap break-words font-mono text-slate-700 dark:text-slate-300">{asText(value)}</pre>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  );
}

/** The request id in small monospace, with a copy button (section 2.1 field 7). */
export function RequestId({ id }: { id: string }) {
  return (
    <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
      <span>Request</span>
      <code className="font-mono" data-testid="approval-id">{id}</code>
      <button
        type="button"
        aria-label={`Copy request id ${id}`}
        title="Copy the request id"
        className="rounded-control p-0.5 hover:bg-slate-100 dark:hover:bg-slate-800"
        onClick={() => {
          try {
            void navigator.clipboard?.writeText(id);
          } catch {
            // No clipboard (an insecure page): the id is on screen to select by hand.
          }
        }}
      >
        <Copy size={12} aria-hidden="true" />
      </button>
    </div>
  );
}

interface ApprovalCardProps {
  request: Approval;
  now: number;
  /** Called once a decision went through (true) or was refused (false). */
  onDecided?: (ok: boolean) => void;
}

/** One pending request with its Approve and Reject buttons (the banner and the Approvals screen). */
export const ApprovalCard = forwardRef<HTMLLIElement, ApprovalCardProps>(function ApprovalCard({ request, now, onDecided }, ref) {
  const approve = useApprovalsStore((s) => s.approve);
  const reject = useApprovalsStore((s) => s.reject);
  const error = useApprovalsStore((s) => (s.errorId === request.id ? s.error : null));
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const reasonRef = useRef<HTMLInputElement>(null);
  const label = actionLabel(request.action);
  const facts = (request.payload as { facts?: AnnotationFacts & TargetFacts }).facts;
  const titleId = `approval-${request.id}-title`;

  useEffect(() => {
    if (rejecting) reasonRef.current?.focus();
  }, [rejecting]);

  const run = async (call: () => Promise<boolean>) => {
    setBusy(true);
    const ok = await call();
    setBusy(false);
    if (ok) {
      setRejecting(false);
      setReason('');
    }
    onDecided?.(ok);
  };

  return (
    <li
      ref={ref}
      tabIndex={-1}
      aria-labelledby={titleId}
      className="rounded-control bg-white px-3 py-2 text-xs text-slate-700 outline-none focus-visible:ring-2 focus-visible:ring-amber-500 dark:bg-slate-900 dark:text-slate-300"
      data-testid="approval-row"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span id={titleId} className="text-sm font-medium text-slate-900 dark:text-slate-100">{label}</span>
        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:text-amber-300" data-testid="approval-pill">
          Waiting
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-600 dark:text-slate-400">
        <AskedBy who={request.requested_by} />
        <LocalTime iso={request.created_at} prefix="asked " />
        <span title={isoInstant(request.expires_at)} data-testid="approval-expiry">{expiresIn(request.expires_at, now)}</span>
      </div>
      <dl className="mt-1" data-testid="approval-facts">
        <dt className="sr-only">Summary</dt>
        <dd className="whitespace-pre-wrap break-words">{request.summary}</dd>
      </dl>
      {request.action === 'source_annotate' && facts && <AnnotationCard facts={facts} />}
      {request.action === 'gate_target_write' && facts && Array.isArray(facts.verdict_changes) && <TargetCard facts={facts} />}
      {request.action === 'paid_classifier_run' && paidFacts(request.payload) && (
        <PaidRunCard facts={paidFacts(request.payload) as PaidRunFacts} />
      )}
      {request.action === 'code_rule_save' && facts && (
        <CodeRuleCard facts={facts as CodeRuleFacts} source={codeRuleSource(request.payload)} />
      )}
      <AllArguments payload={request.payload} />
      <RequestId id={request.id} />
      {error && (
        <p role="alert" className="mt-1 text-red-700 dark:text-red-400" data-testid="approval-card-error">
          {error}
        </p>
      )}
      {rejecting ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            ref={reasonRef}
            type="text"
            value={reason}
            maxLength={REJECT_REASON_MAX}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)"
            aria-label="Reason (optional)"
            className="min-w-48 flex-1 rounded-control border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-600 dark:bg-slate-950"
            data-testid="reject-reason"
          />
          <Button size="sm" variant="danger" disabled={busy} onClick={() => void run(() => reject(request.id, reason))}>
            Confirm reject
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setRejecting(false); setReason(''); }}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy} onClick={() => void run(() => approve(request.id))}>
            Approve {label.toLowerCase()}
          </Button>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => setRejecting(true)}>
            Reject
          </Button>
        </div>
      )}
    </li>
  );
});

/** The waiting cards, in the store's order, with focus kept on a card after a refused decision
 *  and moved to the next card after one that went through (section 2.7). */
export function ApprovalCardList({ requests, now, className = 'stack-y-2' }: { requests: Approval[]; now: number; className?: string }) {
  const refs = useRef(new Map<string, HTMLLIElement>());
  const latest = useRef(requests);
  useEffect(() => {
    latest.current = requests;
  }, [requests]);

  /** After the list has re-rendered: focus the card now at `index` (the next one), if any. */
  const focusAt = (index: number) =>
    window.setTimeout(() => {
      const list = latest.current;
      const target = list[Math.min(index, list.length - 1)];
      if (target) refs.current.get(target.id)?.focus();
    }, 0);

  return (
    <ul className={className}>
      {requests.map((request, index) => (
        <ApprovalCard
          key={request.id}
          ref={(el) => {
            if (el) refs.current.set(request.id, el);
            else refs.current.delete(request.id);
          }}
          request={request}
          now={now}
          onDecided={(ok) => {
            if (ok) focusAt(index);
            else refs.current.get(request.id)?.focus();
          }}
        />
      ))}
    </ul>
  );
}

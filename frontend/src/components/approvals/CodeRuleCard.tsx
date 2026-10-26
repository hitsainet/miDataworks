// The approval card of a `code_rule_save` request (feature 018, FR-018.43; FTDD section 7.9; FTID
// section 6). An agent asked to save a code rule: Python that runs, isolated, over dataset text.
// Approving it approves THIS exact code (its hash), so the card shows everything the hash covers
// and what the operator needs to decide: the standing of the hash first (never approved, or
// revoked by you, when and why), then the full source, the change against the previous version,
// the functions, the parameter schema, the case results, the imports and the full hash. No editor.
//
// The shape is meant to be followed by miStudio's and miLLM's approval pages (ENH-008 D4): read
// `payload.facts` (save.approval_facts) plus `payload.body.document.source` (the body is what
// approving replays and hashes); put the hash's CURRENT state at the top (read fresh: the facts are
// a snapshot from when the agent asked), never green for "revoked" or "never approved"; say that
// approving a revoked hash's request is refused (D60); render the source as text nodes only.
//
// Privacy: the source is shown to the operator (that is the point of the card) and is never
// logged; nothing here calls console or sends the source anywhere.
import { useEffect, useState } from 'react';
import { ShieldAlert, ShieldQuestion } from 'lucide-react';

import { codeRulesApi } from '@/api/codeRules';

import type { ApprovalDecision, CaseOutcome, CaseResult, CodeRuleApprovalState, CodeRuleFacts } from '@/types/codeRules';
import { formatEt } from '@/utils/format';
import { TOKEN_CLASSES, tokenizePython } from '@/utils/highlightPython';

/** The source, highlighted. Each token is a React text node in a <span>: never HTML. */
export function PythonSource({ source, testId = 'code-rule-source' }: { source: string; testId?: string }) {
  return (
    <pre
      className="max-h-96 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-5 text-slate-800 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
      data-testid={testId}
    >
      <code>
        {tokenizePython(source).map((t, i) =>
          t.kind === 'plain' ? t.text : (
            <span key={i} className={TOKEN_CLASSES[t.kind]} data-token={t.kind}>
              {t.text}
            </span>
          ),
        )}
      </code>
    </pre>
  );
}

/** A unified diff, one line per row: additions green, removals red, headers slate. */
export function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, '').split('\n');
  return (
    <pre
      className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-5 dark:border-slate-700 dark:bg-slate-950"
      data-testid="code-rule-diff"
    >
      {lines.map((line, i) => {
        const header = line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@');
        const cls = header
          ? 'text-slate-500 dark:text-slate-400'
          : line.startsWith('+')
            ? 'bg-green-500/10 text-green-800 dark:text-green-300'
            : line.startsWith('-')
              ? 'bg-red-500/10 text-red-800 dark:text-red-300'
              : 'text-slate-700 dark:text-slate-300';
        return (
          <div key={i} className={cls} data-diff={header ? 'header' : line[0] === '+' ? 'add' : line[0] === '-' ? 'remove' : 'same'}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

export function outcomeText(o: CaseOutcome | undefined): string {
  if (!o) return 'not recorded';
  if (typeof o.count === 'number') return `count ${o.count}`;
  if (o.reason) return `reason ${o.reason}`;
  return 'not recorded';
}

export function CaseResultsTable({ results }: { results: CaseResult[] }) {
  if (results.length === 0) return <p className="text-xs text-slate-500 dark:text-slate-400">No case results recorded.</p>;
  return (
    <table className="w-full text-xs" data-testid="code-rule-case-results">
      <thead>
        <tr className="text-left text-slate-500 dark:text-slate-400">
          <th className="py-0.5 pr-2 font-medium">Case</th>
          <th className="py-0.5 pr-2 font-medium">Function</th>
          <th className="py-0.5 pr-2 font-medium">Expected</th>
          <th className="py-0.5 pr-2 font-medium">Got</th>
          <th className="py-0.5 font-medium">Result</th>
        </tr>
      </thead>
      <tbody>
        {results.map((r) => (
          <tr key={r.id} className="border-t border-slate-200 dark:border-slate-700">
            <td className="py-0.5 pr-2 font-mono">{r.id}</td>
            <td className="py-0.5 pr-2 font-mono">{r.function}</td>
            <td className="py-0.5 pr-2">{outcomeText(r.expected)}</td>
            <td className="py-0.5 pr-2">{outcomeText(r.result)}</td>
            <td className={`py-0.5 font-medium ${r.passed ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
              {r.passed ? 'passed' : 'failed'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The hash's standing, worded for the operator. "approved" is the only state drawn green. */
export function ApprovalStateNote({
  state,
  last,
  context = 'screen',
}: {
  state: CodeRuleApprovalState | undefined;
  last?: ApprovalDecision | null;
  /** 'request': an agent's pending save (the backend refuses to save a revoked hash, D60);
   *  'screen': the Code rules screen, where Approve again is the one way back. */
  context?: 'request' | 'screen';
}) {
  const when = formatEt(last?.created_at) ?? 'time not recorded';
  if (state === 'revoked') {
    return (
      <div
        className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-800 dark:text-red-300"
        role="note"
        data-testid="code-rule-state"
        data-state="revoked"
      >
        <ShieldAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
        <span>
          Revoked: this exact code was revoked{last ? ` by ${last.changed_by}, ${when}` : ''}
          {last?.reason ? ` (${last.reason})` : ''}.{' '}
          {context === 'request'
            ? 'Approving this request will be refused: a revoked code comes back only through Approve again on the Code rules screen, with a reason.'
            : 'Only Approve again restores it, after its stored cases pass under the current runner contract.'}
        </span>
      </div>
    );
  }
  if (state === 'approved') {
    return (
      <div className="rounded-md border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm font-medium text-green-800 dark:text-green-300" data-testid="code-rule-state" data-state="approved">
        Approved{last ? ` by ${last.changed_by}, ${when}` : ''}.
      </div>
    );
  }
  return (
    <div
      className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-800 dark:text-amber-300"
      role="note"
      data-testid="code-rule-state"
      data-state={state ?? 'not-recorded'}
    >
      <ShieldQuestion size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span>{state === 'unknown' ? 'Never approved: approving lets this exact code run in rule set previews and builds.' : 'Approval state not recorded.'}</span>
    </div>
  );
}

const h = 'mt-3 mb-1 text-xs font-semibold text-slate-800 dark:text-slate-200';

type LiveState = { kind: 'loading' } | { kind: 'error' } | { kind: 'read'; state: CodeRuleApprovalState; last: ApprovalDecision | null };

/** The hash's standing NOW. `facts.approval_state` is a snapshot from when the agent asked (and a
 *  revoked hash is never queued, D60), so a revocation written while the request waits shows only
 *  in a fresh read (018 S5 review). */
function useLiveApprovalState(sha: string | undefined): LiveState {
  const [live, setLive] = useState<{ sha: string; value: LiveState } | null>(null);
  useEffect(() => {
    if (!sha) return;
    let cancelled = false;
    codeRulesApi
      .approvalState(sha)
      .then((out) => !cancelled && setLive({ sha, value: { kind: 'read', state: out.state, last: out.history?.[0] ?? null } }))
      .catch(() => !cancelled && setLive({ sha, value: { kind: 'error' } }));
    return () => {
      cancelled = true;
    };
  }, [sha]);
  if (!sha) return { kind: 'error' };
  return live && live.sha === sha ? live.value : { kind: 'loading' };
}

export function CodeRuleCard({ facts, source }: { facts: CodeRuleFacts; source: string | undefined }) {
  // The source shown is the one in the request body: the body is what approving replays and hashes.
  const text = source;
  const imports = facts.imports ?? [];
  const functions = facts.functions ?? [];
  const live = useLiveApprovalState(facts.code_sha256);
  return (
    <div className="mt-2 text-xs text-slate-700 dark:text-slate-300" data-testid="code-rule-card">
      {live.kind === 'read' ? (
        <ApprovalStateNote state={live.state} last={live.last} context="request" />
      ) : (
        <div data-testid="code-rule-state-snapshot">
          <ApprovalStateNote state={facts.approval_state} last={facts.last_decision} context="request" />
          <p className="mt-1 text-slate-500 dark:text-slate-400" data-testid="code-rule-state-age" data-live={live.kind}>
            {live.kind === 'loading'
              ? 'This is the state when the agent asked; reading the current state.'
              : 'The current state could not be read; this is the state when the agent asked.'}
          </p>
        </div>
      )}
      <div className="mt-2">
        Code rule <span className="font-medium">{facts.name}</span> for behaviour{' '}
        <span className="font-mono">{facts.behaviour_key}</span>
        {facts.contract_version ? `, runner contract ${facts.contract_version}` : ''}.
      </div>
      <div className="mt-1 break-all">
        Hash (SHA-256) <span className="font-mono" data-testid="code-rule-hash">{facts.code_sha256 ?? 'not recorded'}</span>
      </div>
      <h4 className={h}>Source</h4>
      {text !== undefined ? <PythonSource source={text} /> : <p className="text-amber-800 dark:text-amber-300">The request does not carry the source; do not approve it.</p>}
      <h4 className={h}>Change from the previous version</h4>
      {facts.previous ? (
        facts.diff ? (
          <>
            <p className="mb-1 text-slate-500 dark:text-slate-400">
              Against version {facts.previous.version} (<span className="font-mono">{facts.previous.code_sha256.slice(0, 12)}</span>).
            </p>
            <DiffView diff={facts.diff} />
          </>
        ) : (
          <p data-testid="code-rule-diff-none">The source is the same as version {facts.previous.version}.</p>
        )
      ) : (
        <p data-testid="code-rule-diff-none">This is the first version of this name.</p>
      )}
      <h4 className={h}>Functions</h4>
      <p className="font-mono" data-testid="code-rule-functions">{functions.length ? functions.join(', ') : 'none'}</p>
      <h4 className={h}>Parameter schema</h4>
      <pre className="max-h-48 overflow-auto rounded-md bg-slate-50 p-2 font-mono dark:bg-slate-950" data-testid="code-rule-params-schema">
        {facts.params_schema === undefined ? 'none' : JSON.stringify(facts.params_schema, null, 2)}
      </pre>
      <h4 className={h}>Case results</h4>
      <CaseResultsTable results={facts.case_results ?? []} />
      <h4 className={h}>Imports</h4>
      <p className="font-mono" data-testid="code-rule-imports">{imports.length ? imports.join(', ') : 'none'}</p>
    </div>
  );
}

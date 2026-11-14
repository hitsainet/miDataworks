// The Code rules screen (feature 018, FR-018.44; FTDD section 7.9; FTID section 6). Code rules are
// written by agents (MCP) and the REST routes; this screen reads them and holds the two decisions
// that are the operator's alone: Revoke an approved hash, and Approve again a revoked one (the ONE
// way a revoked hash returns). Each takes a reason, asks for the Settings PIN when one is set (the
// client's PIN flow, api/client.ts), and is one POST. Approve again re-runs the stored cases under
// the current contract first; a failing case (409 code_rule_case_failed) or an unreachable runner
// (503) changes nothing, and the screen says which. Honest states ("not recorded", "revoked",
// "failing under contract ...", "runner unavailable") are amber, red or slate, never green. Times
// are US Eastern.
import { useEffect, useState } from 'react';

import { ApprovalStateNote, CaseResultsTable, PythonSource, outcomeText } from '@/components/approvals/CodeRuleCard';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useCodeRulesStore, type CodeRuleRefusal } from '@/stores/codeRulesStore';
import { useHealthStore } from '@/stores/healthStore';
import type { CodeRuleApprovalState, CodeRuleDetail, ContractStatus } from '@/types/codeRules';
import { formatEt, shortId } from '@/utils/format';

const card = 'rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';
const th = 'text-left font-medium text-slate-500 dark:text-slate-400 px-2 py-1';
const td = 'px-2 py-1 align-top';
const h3 = 'font-medium mt-4 mb-1 text-slate-900 dark:text-slate-100';

/** The fixed sentence the screen must carry (FTDD section 7.9). */
export const COUNT_ONLY_SENTENCE =
  "A code rule can only report a count; a dry run's error details are shown only for the agent's own test text.";

function StateBadge({ state }: { state: CodeRuleApprovalState }) {
  if (state === 'approved') return <Badge variant="success">approved</Badge>;
  if (state === 'revoked') return <Badge variant="danger">revoked</Badge>;
  return <Badge variant="warning">never approved</Badge>;
}

/** The latest contract run, in words. Only "passed under contract x" is green. */
export function ContractLine({ status }: { status: ContractStatus | null }) {
  if (!status) return <span className="text-slate-500 dark:text-slate-400" data-testid="contract-status" data-state="not-recorded">Cases under the runner contract: not recorded</span>;
  const when = formatEt(status.run_at);
  if (status.passed) {
    return (
      <span className="text-green-700 dark:text-green-400" data-testid="contract-status" data-state="passed">
        Every case passed under contract {status.contract_version}{when ? `, ${when}` : ''}
      </span>
    );
  }
  return (
    <span className="text-amber-800 dark:text-amber-300" data-testid="contract-status" data-state="failing">
      Failing under contract {status.contract_version}
      {status.failing_case ? `: case ${status.failing_case}` : ''}
      {status.reason ? ` (${status.reason})` : ''}
      {when ? `, ${when}` : ''}. New builds refuse it.
    </span>
  );
}

/** What a refused Revoke or Approve again means, said plainly. Never green. */
export function RefusalNote({ refusal }: { refusal: CodeRuleRefusal }) {
  const d = refusal.details;
  let text = refusal.message;
  if (refusal.code === 'code_rule_case_failed') {
    text = `Not approved: case ${String(d.case)} expected ${outcomeText(d.expected as never)} and got ${outcomeText(d.actual as never)} under runner contract ${String(d.contract_version ?? 'unknown')}. Nothing changed: the code is still not approved.`;
  } else if (refusal.status === 503 || refusal.code === 'runner_unavailable') {
    text = `The runner is unavailable, so the cases could not run: ${refusal.message} Nothing changed.`;
  } else if (refusal.code === 'PIN_REQUIRED') {
    text = `${refusal.message} Nothing changed.`;
  }
  return (
    <p role="alert" className="mt-2 rounded-control border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-300" data-testid="code-rule-refusal" data-code={refusal.code}>
      {text}
    </p>
  );
}

function DecisionForm({ detail, kind, onDone }: { detail: CodeRuleDetail; kind: 'revoke' | 'approve'; onDone: () => void }) {
  const revoke = useCodeRulesStore((s) => s.revoke);
  const approveAgain = useCodeRulesStore((s) => s.approveAgain);
  const refusal = useCodeRulesStore((s) => s.refusal);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const act = async () => {
    setBusy(true);
    const ok = kind === 'revoke' ? await revoke(detail.code_sha256, reason) : await approveAgain(detail.code_sha256, reason);
    setBusy(false);
    if (ok) onDone();
  };
  return (
    <div className="mt-3 rounded-control border border-slate-200 dark:border-slate-700 p-3" data-testid={`code-rule-${kind}-form`}>
      <p className="text-sm text-slate-700 dark:text-slate-300 mb-2">
        {kind === 'revoke'
          ? 'Revoking stops rule set previews and builds from running this exact code. Versions already built with it keep their record.'
          : 'Approving again runs every stored case under the current runner contract first; if one fails, or the runner cannot be reached, nothing changes.'}{' '}
        Asks for the Settings personal identification number (PIN) when one is set.
      </p>
      <label htmlFor={`code-rule-${kind}-reason`} className="block text-xs font-medium mb-1 text-slate-800 dark:text-slate-200">
        Reason (kept in this code's approval history)
      </label>
      <textarea
        id={`code-rule-${kind}-reason`}
        className="w-full rounded-control px-3 py-2 text-sm border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100"
        rows={2}
        maxLength={500}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {refusal && <RefusalNote refusal={refusal} />}
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant={kind === 'revoke' ? 'danger' : 'primary'} disabled={!reason.trim() || busy} loading={busy} onClick={() => void act()}>
          {kind === 'revoke' ? 'Revoke this code' : 'Approve this code again'}
        </Button>
        <Button size="sm" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Detail({ detail }: { detail: CodeRuleDetail }) {
  const archive = useCodeRulesStore((s) => s.archive);
  const unarchive = useCodeRulesStore((s) => s.unarchive);
  const clearRefusal = useCodeRulesStore((s) => s.clearRefusal);
  const lastCaseResults = useCodeRulesStore((s) => s.lastCaseResults);
  const [deciding, setDeciding] = useState<'revoke' | 'approve' | null>(null);
  const state = detail.approval.state;
  const history = detail.approval.history;
  const startDeciding = (kind: 'revoke' | 'approve') => {
    clearRefusal();
    setDeciding(kind);
  };
  return (
    <section className={card} data-testid="code-rule-detail" aria-labelledby="code-rule-detail-title">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 id="code-rule-detail-title" className="text-lg font-semibold text-slate-900 dark:text-slate-100">{detail.name}</h2>
        <Badge variant="neutral">version {detail.version}</Badge>
        <Badge variant="primary">{detail.behaviour_key}</Badge>
        {detail.archived && <Badge variant="warning">Archived</Badge>}
        {detail.superseded_by != null && <Badge variant="neutral">version {detail.superseded_by} is newer</Badge>}
      </div>
      <ApprovalStateNote state={state} last={history[0]} />
      <p className="mt-2 text-xs break-all text-slate-600 dark:text-slate-300">
        Hash (SHA-256) <span className="font-mono">{detail.code_sha256}</span>
      </p>
      <p className="mt-1 text-xs"><ContractLine status={detail.contract_status} /></p>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Saved by {detail.created_by}{detail.created_by_origin === 'agent' ? ' (an agent)' : ''}, {formatEt(detail.created_at) ?? 'time not recorded'}.
        {detail.approval.pending_approvals.length > 0 && ` ${detail.approval.pending_approvals.length} agent request(s) for this code wait on the Approvals screen.`}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {state === 'approved' ? (
          <Button size="sm" variant="danger" onClick={() => startDeciding('revoke')} data-testid="code-rule-revoke">
            Revoke
          </Button>
        ) : (
          <Button size="sm" onClick={() => startDeciding('approve')} data-testid="code-rule-approve-again">
            Approve again
          </Button>
        )}
        <Button size="sm" variant="secondary" onClick={() => void (detail.archived ? unarchive(detail.name) : archive(detail.name))}>
          {detail.archived ? 'Unarchive' : 'Archive'}
        </Button>
      </div>
      {deciding && <DecisionForm detail={detail} kind={deciding} onDone={() => setDeciding(null)} />}
      {lastCaseResults && (
        <div className="mt-3" data-testid="code-rule-recheck">
          <h3 className={h3}>Cases run for this approval</h3>
          <CaseResultsTable results={lastCaseResults} />
        </div>
      )}
      <h3 className={h3}>Source</h3>
      <PythonSource source={detail.document.source} testId="code-rule-detail-source" />
      <h3 className={h3}>Functions</h3>
      <p className="font-mono text-sm">{detail.document.functions.map((f) => f.name).join(', ') || 'none'}</p>
      <h3 className={h3}>Parameter schema</h3>
      <pre className="max-h-48 overflow-auto rounded-control bg-slate-50 p-2 font-mono text-xs dark:bg-slate-950">
        {detail.document.params_schema === undefined ? 'none' : JSON.stringify(detail.document.params_schema, null, 2)}
      </pre>
      <h3 className={h3}>Cases ({detail.cases.length})</h3>
      <ul className="text-sm list-disc ml-5">
        {detail.cases.map((c) => (
          <li key={c.id}>
            <span className="font-mono">{c.id}</span> ({c.function}): expects {outcomeText(c.expect)}
          </li>
        ))}
      </ul>
      <h3 className={h3}>Approval history{detail.approval.history_rows > history.length ? ` (latest ${history.length} of ${detail.approval.history_rows})` : ''}</h3>
      {history.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">Not recorded: this code has never been approved or revoked.</p>
      ) : (
        <ul className="text-sm list-disc ml-5" data-testid="code-rule-history">
          {history.map((d) => (
            <li key={d.id}>
              {d.action === 'approve' ? 'Approved' : 'Revoked'} by {d.changed_by}, {formatEt(d.created_at) ?? 'time not recorded'}: {d.reason}
            </li>
          ))}
        </ul>
      )}
      <h3 className={h3}>Builds that used it</h3>
      {detail.used_by.versions.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No build has run this version yet.</p>
      ) : (
        <ul className="text-sm list-disc ml-5">
          {detail.used_by.versions.map((u) => (
            <li key={`${u.version_id}-${u.step_index}`}>
              version {u.number} ({shortId(u.version_id)}), step {u.step_index}, through rule set {u.rule_set}; {u.failed_rows} row(s) failed
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RunnerLine() {
  const health = useHealthStore((s) => s.health);
  const enabled = useCodeRulesStore((s) => s.enabled);
  const runner = health?.dependencies.runner;
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="code-rules-runtime">
      {enabled === false && (
        <span className="text-amber-800 dark:text-amber-300" data-state="disabled">Code rules are switched off in this deployment: tests, saves and builds refuse them.</span>
      )}
      {!runner ? (
        <span className="text-slate-500 dark:text-slate-400" data-state="runner-not-recorded">Runner: not recorded</span>
      ) : runner.ok ? (
        <span className="text-slate-600 dark:text-slate-300" data-state="runner-ok">Runner: reachable</span>
      ) : (
        <span className="text-amber-800 dark:text-amber-300" data-state="runner-unavailable">Runner unavailable{runner.reason ? `: ${runner.reason}` : ''}</span>
      )}
    </div>
  );
}

export function CodeRulesPanel({ panel }: { panel: PanelDef }) {
  const items = useCodeRulesStore((s) => s.items);
  const current = useCodeRulesStore((s) => s.current);
  const error = useCodeRulesStore((s) => s.error);
  const notice = useCodeRulesStore((s) => s.notice);
  const fetchList = useCodeRulesStore((s) => s.fetchList);
  const open = useCodeRulesStore((s) => s.open);
  const fetchContract = useCodeRulesStore((s) => s.fetchContract);
  const [behaviourKey, setBehaviourKey] = useState('');
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    void fetchContract();
  }, [fetchContract]);
  useEffect(() => {
    void fetchList(behaviourKey, showArchived);
  }, [fetchList, behaviourKey, showArchived]);

  return (
    <>
      <PageHead help="code_rules" title={panel.title} subtitle={panel.subtitle} />
      <div className="stack-y-5">
        <section className={card} aria-labelledby="code-rules-list-title" data-testid="code-rules-list">
          <h2 id="code-rules-list-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">Code rules ({items.length})</h2>
          <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="code-rules-count-only">{COUNT_ONLY_SENTENCE}</p>
          <RunnerLine />
          <div className="flex flex-wrap items-end gap-4 my-4">
            <label className="text-sm text-slate-700 dark:text-slate-300">
              Behaviour key
              <input
                className="ml-2 rounded-control border border-slate-300 dark:border-slate-600 bg-transparent px-2 py-1"
                value={behaviourKey}
                onChange={(e) => setBehaviourKey(e.target.value)}
                placeholder="all behaviours"
              />
            </label>
            <label className="text-sm flex items-center gap-2 text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} data-testid="code-rules-show-archived" />
              Show archived code rules
            </label>
          </div>
          {error && <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">{error}</p>}
          {notice && <p role="status" className="mb-2 text-sm text-slate-700 dark:text-slate-200" data-testid="code-rules-notice">{notice}</p>}
          {items.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="code-rules-empty">
              No code rules yet. An agent writes one with its tools; it runs only once you approve it.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className={th}>Name</th>
                  <th className={th}>Behaviour</th>
                  <th className={th}>Version</th>
                  <th className={th}>Approval</th>
                  <th className={th}>Cases under the contract</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.name} className="border-t border-slate-200 dark:border-slate-700" data-testid="code-rule-row">
                    <td className={td}>
                      {r.version ? (
                        <button type="button" className="font-mono text-indigo-700 dark:text-indigo-300 underline" onClick={() => void open(r.name, r.version as number)}>
                          {r.name}
                        </button>
                      ) : (
                        <span className="font-mono">{r.name}</span>
                      )}
                      {r.archived && <span className="ml-2 text-xs text-slate-500">archived</span>}
                    </td>
                    <td className={`${td} font-mono`}>{r.behaviour_key}</td>
                    <td className={td}>{r.version ?? 'not recorded'}</td>
                    <td className={td}><StateBadge state={r.approval_state} /></td>
                    <td className={`${td} text-xs`}><ContractLine status={r.last_case_result} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        {current && <Detail detail={current} />}
      </div>
    </>
  );
}

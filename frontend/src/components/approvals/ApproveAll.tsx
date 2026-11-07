// "Approve all settings changes (N)" (ENH-006 D79, operator 2026-10-29: "When approving one or more
// settings changes, I want to approve all outstanding settings changes with one approval request.")
// and "Approve all code rules (N)" (D81): the same mechanics, a separate button and batch; the two
// kinds are never mixed. A code rule's confirm lists what the operator approves: the rule's name and
// version, the first 12 hex of its code hash, its functions and its case results at filing. A
// code-rule approval does not ask for the Settings PIN, alone or in a batch (the server's rule).
//
// Shown on the banner and the Approvals screen only while TWO OR MORE settings changes wait (one is
// approved from its own card). The button opens a confirm that lists every request it covers: the
// action, the setting with its value now and the value asked for (from → to), and who asked.
// Confirm sends one call, POST /api/v1/approvals/approve-batch; the server checks every id first and
// approves nothing unless all are waiting settings changes, and asks for the Settings PIN once for
// the whole decision (api/client.ts opens the PIN dialog on its 403 and resends the call once).
// There is no "reject all". Which requests count is the server's call (`settings_change`: a
// request whose route is behind the Settings PIN); the page does not guess from the action name.
// Every piece of request text is a React text node (ENH-008 section 2.7).
import { useState } from 'react';

import { settingsApi, rolesApi } from '@/api/endpoints';
import { AskedBy, actionLabel, asText } from '@/components/approvals/ApprovalCard';
import { Button } from '@/components/common/Button';
import { BATCH_NOUN, codeRuleRequests, settingsChanges, useApprovalsStore } from '@/stores/approvalsStore';
import type { Approval, BatchKind, EndpointRole, EnvironmentRow, Setting } from '@/types/api';
import type { CodeRuleFacts } from '@/types/codeRules';

/** The batch button appears from this many waiting requests of its kind. */
export const BATCH_MIN = 2;

export interface Current {
  environment: EnvironmentRow[] | null;
  settings: Setting[] | null;
  roles: EndpointRole[] | null;
}

export interface SettingChange {
  subject: string;
  from: string;
  to: string;
}

const SECRET = 'a secret (not shown)';

function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'not set';
  return asText(value);
}

function body(payload: Record<string, unknown>): Record<string, unknown> {
  const b = payload.body;
  return b && typeof b === 'object' ? (b as Record<string, unknown>) : {};
}

function endpointText(baseUrl: unknown, modelId: unknown): string {
  const server = typeof baseUrl === 'string' && baseUrl ? baseUrl : 'no server';
  return typeof modelId === 'string' && modelId ? `${server}, model ${modelId}` : server;
}

/** What one settings change changes: the setting, its value now (read when the confirm opened) and
 *  the value the agent asked for. Secrets are never shown; an unknown current value says so. */
export function describeSettingChange(request: Approval, current: Current): SettingChange {
  const payload = request.payload ?? {};
  const route = request.target.split('.').pop() ?? '';
  if (route === 'put_setting' || route === 'delete_setting') {
    const key = String(payload.key ?? '?');
    const env = current.environment?.find((r) => r.key === key);
    const stored = current.settings?.find((s) => s.key === key);
    const secret = request.action === 'secret_write' || env?.secret === true || stored?.is_sensitive === true;
    const subject = env?.label ? `${env.label} (${key})` : key;
    let from = 'unknown';
    if (env) from = secret ? (env.is_set ? SECRET : 'not set') : shown(env.value);
    else if (stored) from = secret ? (stored.is_set ? SECRET : 'not set') : shown(stored.value);
    if (route === 'delete_setting') {
      const to = secret ? 'cleared' : env ? `the environment value (${shown(env.env_value)})` : 'the environment value';
      return { subject, from, to };
    }
    return { subject, from, to: secret ? `a new secret (not shown)` : shown(body(payload).value) };
  }
  if (route === 'put_role') {
    const role = String(payload.role ?? '?');
    const now = current.roles?.find((r) => r.role === role);
    const b = body(payload);
    const key = request.action === 'secret_write' ? ' and a new API key (not shown)' : '';
    return {
      subject: `the ${role} endpoint`,
      from: now ? endpointText(now.base_url, now.model_id) : 'unknown',
      to: `${endpointText(b.base_url, b.model_id)}${key}`,
    };
  }
  return { subject: request.summary, from: 'unknown', to: request.summary };
}

async function readCurrent(): Promise<Current> {
  const [environment, settings, roles] = await Promise.allSettled([
    settingsApi.environment(),
    settingsApi.list(),
    rolesApi.list(),
  ]);
  return {
    environment: environment.status === 'fulfilled' ? environment.value.settings : null,
    settings: settings.status === 'fulfilled' ? settings.value : null,
    roles: roles.status === 'fulfilled' ? roles.value : null,
  };
}

export interface CodeRuleItem {
  name: string;
  version: string;
  sha12: string;
  functions: string;
  cases: { id: string; fn: string; passed: boolean }[];
}

/** What one code-rule request asks to approve, from the facts stored when the agent filed it. */
export function describeCodeRule(request: Approval): CodeRuleItem {
  const facts = ((request.payload ?? {}) as { facts?: CodeRuleFacts }).facts ?? {};
  const name = typeof facts.name === 'string' ? facts.name : request.summary;
  const previous = facts.previous?.version;
  const version = typeof previous === 'number' ? `version ${previous + 1} (after ${previous})` : 'version 1 (new)';
  const sha12 = typeof facts.code_sha256 === 'string' ? facts.code_sha256.slice(0, 12) : 'unknown';
  const functions = Array.isArray(facts.functions) && facts.functions.length > 0 ? facts.functions.join(', ') : 'none listed';
  const cases = (Array.isArray(facts.case_results) ? facts.case_results : []).map((c) => ({
    id: String(c.id),
    fn: String(c.function),
    passed: c.passed === true,
  }));
  return { name, version, sha12, functions, cases };
}

const TITLE: Record<BatchKind, string> = {
  settings: 'Approve all settings changes',
  code_rules: 'Approve all code rules',
};

function SettingsItem({ request, current }: { request: Approval; current: Current | null }) {
  const change = current ? describeSettingChange(request, current) : null;
  return (
    <div className="whitespace-pre-wrap break-words" data-testid="approve-all-change">
      {change ? (
        <>
          {change.subject}: {change.from} → {change.to}
        </>
      ) : (
        request.summary
      )}
    </div>
  );
}

function CodeRuleItemView({ request }: { request: Approval }) {
  const item = describeCodeRule(request);
  const passed = item.cases.filter((c) => c.passed).length;
  return (
    <div className="break-words" data-testid="approve-all-code-rule">
      <div data-testid="approve-all-code-rule-head">
        {item.name}, {item.version}, code <code className="font-mono">{item.sha12}</code>
      </div>
      <div>Functions: {item.functions}</div>
      <div data-testid="approve-all-cases">
        Cases at filing: {passed} of {item.cases.length} passed
        {item.cases.length > 0 && (
          <ul className="list-disc pl-5">
            {item.cases.map((c) => (
              <li key={c.id}>
                {c.id} ({c.fn}): {c.passed ? 'passed' : 'FAILED'}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** One batch button, its confirm and the outcome line. Draws nothing below {@link BATCH_MIN}. */
export function ApproveAllBatch({ pending, kind }: { pending: Approval[]; kind: BatchKind }) {
  const requests = kind === 'settings' ? settingsChanges(pending) : codeRuleRequests(pending);
  const approveBatch = useApprovalsStore((s) => s.approveBatch);
  const ownOutcome = useApprovalsStore((s) => s.batchKind === kind);
  const notice = useApprovalsStore((s) => s.batchNotice);
  const error = useApprovalsStore((s) => s.batchError);
  // The ids the confirm was opened for: Confirm approves exactly these, never a later arrival.
  const [confirming, setConfirming] = useState<Approval[] | null>(null);
  const [current, setCurrent] = useState<Current | null>(null);
  const [busy, setBusy] = useState(false);
  const plural = BATCH_NOUN[kind][1];
  const testid = kind === 'settings' ? 'approve-all-settings' : 'approve-all-code-rules';

  const open = async () => {
    setConfirming(requests);
    setCurrent(null);
    if (kind === 'settings') setCurrent(await readCurrent());
  };

  const confirm = async () => {
    if (!confirming) return;
    setBusy(true);
    await approveBatch(kind, confirming.map((a) => a.id));
    setBusy(false);
    setConfirming(null);
  };

  const outcome = ownOutcome && (notice || error) && (
    <p
      role={error ? 'alert' : 'status'}
      className={`mt-2 text-xs ${error ? 'text-red-700 dark:text-red-400' : 'text-slate-700 dark:text-slate-300'}`}
      data-testid={`${testid}-outcome`}
    >
      {error ?? notice}
    </p>
  );

  if (requests.length < BATCH_MIN && !confirming) return outcome || null;

  return (
    <div className="mt-2" data-testid={testid}>
      {!confirming ? (
        <Button size="sm" onClick={() => void open()} data-testid={`${testid}-button`}>
          {TITLE[kind]} ({requests.length})
        </Button>
      ) : (
        <div
          role="dialog"
          aria-label={TITLE[kind]}
          className="rounded-card border border-amber-500/40 bg-white p-3 text-xs text-slate-700 dark:bg-slate-900 dark:text-slate-300"
          data-testid={`${testid}-confirm`}
        >
          <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
            Approve these {confirming.length} {plural} in one decision?
          </p>
          <p className="mt-0.5 text-slate-500 dark:text-slate-400">
            {kind === 'settings'
              ? 'Each runs once, exactly as shown, and is recorded on its own in History. If the Settings PIN is set, it is asked for once.'
              : 'You approve code: each rule is saved once, exactly as shown, its code hash is approved and the decision is recorded on its own in History.'}
          </p>
          <ul className="mt-2 stack-y-1" data-testid="approve-all-list">
            {confirming.map((request) => (
              <li key={request.id} className="rounded-control bg-amber-500/10 px-2 py-1" data-testid="approve-all-item">
                <div className="font-medium text-slate-900 dark:text-slate-100">{actionLabel(request.action)}</div>
                {kind === 'settings' ? <SettingsItem request={request} current={current} /> : <CodeRuleItemView request={request} />}
                <div className="text-slate-500 dark:text-slate-400">
                  <AskedBy who={request.requested_by} /> · <code className="font-mono">{request.id}</code>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={busy} onClick={() => void confirm()} data-testid={`${testid}-confirm-button`}>
              Confirm: approve all {confirming.length}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {outcome}
    </div>
  );
}

/** Both batch buttons, each over its own kind (the banner and the Approvals screen). */
export function ApproveAllButtons({ pending }: { pending: Approval[] }) {
  return (
    <>
      <ApproveAllBatch pending={pending} kind="settings" />
      <ApproveAllBatch pending={pending} kind="code_rules" />
    </>
  );
}

// "Approve all settings changes (N)" (ENH-006 D79) and "Approve all code rules (N)" (D81). Runs the REAL <App/> (banner, Approvals screen,
// store, API client, PIN dialog) against a fetch stub. Negative controls are recorded in
// 0xcc/reviews/approve_all_settings_controls_2026-10-29.md: the button shown from one waiting
// change, the batch call sending only the first id, the PIN resend removed, the banner or screen
// mount removed; each turns this file red.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { PIN_SESSION_HEADER, setPinSessionToken } from '@/api/pinSession';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { usePinStore } from '@/stores/pinStore';
import type { Approval } from '@/types/api';
import { HEALTH, mockFetch } from '@/test/fetchMock';

import { describeCodeRule, describeSettingChange } from './ApproveAll';

const BATCH = '/api/v1/approvals/approve-batch';

const base = (id: string, over: Partial<Approval> = {}): Approval => ({
  id, action: 'config_write', target: 'src.api.v1.endpoints.settings.put_setting', summary: `summary ${id}`,
  payload: {}, request_digest: '0', requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 2 * 3600_000).toISOString(), decided_by: null,
  created_at: new Date().toISOString(), archived_at: null, settings_change: true, ...over,
});

const LOG = base('apr_log', {
  summary: "Change the setting log_level to 'DEBUG'",
  payload: { key: 'log_level', body: { value: 'DEBUG', reason: null } },
});
const JUDGE = base('apr_judge', {
  target: 'src.api.v1.endpoints.endpoint_roles.put_role',
  summary: 'Change the judge endpoint (server http://judge.test)',
  payload: { role: 'judge', body: { protocol: 'openai_chat', base_url: 'http://judge.test/v1', model_id: 'jev-9b' } },
});
const TOKEN = base('apr_token', {
  action: 'secret_write',
  summary: 'Write the secret setting hf_token',
  payload: { key: 'hf_token', body: { value: 'hmac-sha256:abc', reason: null } },
});
const PUSH = base('apr_push', {
  action: 'hub_push', target: 'src.api.v1.endpoints.publishing.push', summary: 'Push version 3 to the Hub',
  settings_change: false,
});

const ENVIRONMENT = {
  settings: [
    { key: 'log_level', label: 'Log level', value: 'INFO', env_value: 'INFO', secret: false, is_set: true },
    { key: 'hf_token', label: 'Hugging Face token', value: '••••', env_value: null, secret: true, is_set: true },
  ],
  history: [],
};
const ROLES = [{ role: 'judge', base_url: 'http://old-judge.test/v1', model_id: 'old-model' }];

/** A small approvals server with the batch route; `batch` answers it (default: executes them). */
function server(initial: Approval[], batch?: (body: unknown, n: number) => { status?: number; json?: unknown } | undefined) {
  let all = [...initial];
  let n = 0;
  return mockFetch({
    'GET /api/health': HEALTH,
    'GET /api/v1/jobs': { jobs: [] },
    'GET /api/v1/datasets': { items: [], total: 0 },
    'GET /api/v1/approvals?status=pending': () => ({ json: { approvals: all.filter((a) => a.status === 'pending') } }),
    'GET /api/v1/approvals': () => ({ json: { approvals: all } }),
    'GET /api/v1/settings/environment': ENVIRONMENT,
    'GET /api/v1/settings': [],
    'GET /api/v1/endpoint-roles': ROLES,
    'POST /api/v1/settings/pin/session': { token: '1999999999.abcdef0123456789.mac', expires_at: '2033-05-18T03:33:19Z' },
    [`POST ${BATCH}`]: (body: unknown) => {
      const answer = batch?.(body, ++n);
      if (answer) return answer;
      const ids = (body as { approval_ids: string[] }).approval_ids;
      all = all.map((a) => (ids.includes(a.id) ? { ...a, status: 'executed' as const, batch_id: 'apb_1' } : a));
      const kind = (body as { kind: string }).kind;
      return { json: { batch_id: 'apb_1', kind, results: ids.map((id) => ({ approval_id: id, status: 'executed' })) } };
    },
  });
}

beforeEach(() => {
  useApprovalsStore.setState({
    pending: [], error: null, loadError: null, history: [], historyIncludeArchived: false, historyError: null,
    batchKind: null, batchNotice: null, batchError: null,
  });
  usePinStore.setState({ status: null, error: null, notice: null, prompt: null });
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setPinSessionToken(null);
  window.history.replaceState(null, '', '#/');
});

async function openApprovals() {
  window.history.replaceState(null, '', '#/approvals');
  render(<App />);
  return within(await screen.findByTestId('approvals-pending'));
}

describe('the button', () => {
  it('is on the banner of every other screen, counting only the settings changes, as a named action', async () => {
    server([LOG, JUDGE, PUSH]);
    window.history.replaceState(null, '', '#/datasets');
    render(<App />);
    const banner = within(await screen.findByTestId('approvals-banner'));
    expect(await banner.findByRole('button', { name: 'Approve all settings changes (2)' })).toBeInTheDocument();
    expect(banner.queryByRole('button', { name: /^Approve$/ })).toBeNull();
  });

  it('is on the Approvals screen too', async () => {
    server([LOG, JUDGE, TOKEN, PUSH]);
    const waiting = await openApprovals();
    expect(await waiting.findByRole('button', { name: 'Approve all settings changes (3)' })).toBeInTheDocument();
  });

  it('is not shown for ONE waiting settings change, nor for other requests', async () => {
    server([LOG, PUSH, base('apr_other', { action: 'hub_push', settings_change: false })]);
    const waiting = await openApprovals();
    await waiting.findAllByTestId('approval-row');
    expect(screen.queryByTestId('approve-all-settings-button')).toBeNull();
  });
});

describe('the confirm', () => {
  it('lists every request (action, from → to, who asked), then approves them all in ONE call', async () => {
    const { calls } = server([LOG, JUDGE, TOKEN, PUSH]);
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    const confirm = within(screen.getByRole('dialog', { name: 'Approve all settings changes' }));
    const items = confirm.getAllByTestId('approve-all-item');
    expect(items).toHaveLength(3);
    await waitFor(() => expect(confirm.getAllByTestId('approve-all-change')[0]).toHaveTextContent('Log level (log_level): INFO → DEBUG'));
    const changes = confirm.getAllByTestId('approve-all-change').map((el) => el.textContent);
    expect(changes).toContain('the judge endpoint: http://old-judge.test/v1, model old-model → http://judge.test/v1, model jev-9b');
    expect(changes).toContain('Hugging Face token (hf_token): a secret (not shown) → a new secret (not shown)');
    expect(confirm.getAllByText('Environment setting change')).toHaveLength(2);
    expect(confirm.getAllByTestId('who-agent')).toHaveLength(3);
    expect(confirm.queryByText(/Push version 3/)).toBeNull();

    fireEvent.click(confirm.getByTestId('approve-all-settings-confirm-button'));
    expect(await screen.findByTestId('approve-all-settings-outcome')).toHaveTextContent('Approved 3 of 3 settings changes.');
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([{ method: 'POST', path: BATCH, body: { kind: 'settings', approval_ids: ['apr_log', 'apr_judge', 'apr_token'] } }]);
    await waitFor(() => expect(waiting.getAllByTestId('approval-row')).toHaveLength(1));
  });

  it('Cancel approves nothing', async () => {
    const { calls } = server([LOG, JUDGE]);
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.filter((c) => c.method === 'POST')).toEqual([]);
  });

  it('asks for the Settings PIN once for the whole batch and sends it again once with the session', async () => {
    const { calls, fn } = server([LOG, JUDGE, TOKEN], (_body, n) =>
      n === 1
        ? { status: 403, json: { error: { code: 'PIN_REQUIRED', message: 'Settings are locked with a PIN.', details: {} } } }
        : undefined,
    );
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    fireEvent.click(screen.getByTestId('approve-all-settings-confirm-button'));
    fireEvent.change(await screen.findByLabelText('PIN'), { target: { value: '4711' } });
    fireEvent.click(screen.getByTestId('pin-unlock'));
    expect(await screen.findByTestId('approve-all-settings-outcome')).toHaveTextContent('Approved 3 of 3');
    expect(calls.filter((c) => c.path === '/api/v1/settings/pin/session')).toHaveLength(1);
    const sent = fn.mock.calls.filter(([path]) => path === BATCH);
    expect(sent).toHaveLength(2);
    expect((sent[1][1]?.headers as Record<string, string>)[PIN_SESSION_HEADER]).toBe('1999999999.abcdef0123456789.mac');
    expect(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/approve'))).toEqual([]);
  });

  it('says what the server refused, and what did not run', async () => {
    server([LOG, JUDGE], () => ({
      status: 409,
      json: { error: { code: 'APPROVAL_BATCH_REFUSED', message: 'Only waiting settings changes can be approved together; nothing was approved. Refused: apr_judge (not pending (rejected))', details: { refused: [] } } },
    }));
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    fireEvent.click(screen.getByTestId('approve-all-settings-confirm-button'));
    expect(await screen.findByTestId('approve-all-settings-outcome')).toHaveTextContent('nothing was approved. Refused: apr_judge');
  });

  it('reports an item that failed while the others ran', async () => {
    server([LOG, JUDGE], (body) => ({
      json: {
        batch_id: 'apb_2',
        results: (body as { approval_ids: string[] }).approval_ids.map((id, i) =>
          i === 1 ? { approval_id: id, status: 'failed', error: { code: 'X', message: 'The judge is unreachable.' } } : { approval_id: id, status: 'executed' },
        ),
      },
    }));
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    fireEvent.click(screen.getByTestId('approve-all-settings-confirm-button'));
    expect(await screen.findByTestId('approve-all-settings-outcome')).toHaveTextContent(
      'Approved 1 of 2 settings changes. Not done: apr_judge (failed: The judge is unreachable.).',
    );
  });

  it('shows request text as text, never markup', async () => {
    const hostile = base('apr_x', { payload: { key: '<img src=x onerror=alert(1)>', body: { value: '<b>bold</b>' } } });
    server([hostile, LOG]);
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-settings-button'));
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('<img src=x onerror=alert(1)>: unknown → <b>bold</b>'));
    expect(dialog.querySelector('img')).toBeNull();
    expect(dialog.querySelector('b')).toBeNull();
  });
});

describe('describeSettingChange', () => {
  const none = { environment: null, settings: null, roles: null };
  it('never shows a secret, and says when the current value is unknown', () => {
    expect(describeSettingChange(TOKEN, none)).toEqual({ subject: 'hf_token', from: 'unknown', to: 'a new secret (not shown)' });
    expect(describeSettingChange(LOG, none)).toEqual({ subject: 'log_level', from: 'unknown', to: 'DEBUG' });
  });
  it('a reset returns the setting to its environment value', () => {
    const reset = base('apr_reset', { target: 'src.api.v1.endpoints.settings.delete_setting', payload: { key: 'log_level' } });
    expect(describeSettingChange(reset, { ...none, environment: ENVIRONMENT.settings as never })).toEqual({
      subject: 'Log level (log_level)', from: 'INFO', to: 'the environment value (INFO)',
    });
  });
});

// --- code rules (ENH-006 D81) -------------------------------------------------------------------

const rule = (id: string, name: string, previous: number | null, cases: { id: string; function: string; passed: boolean }[]) =>
  base(id, {
    action: 'code_rule_save',
    target: 'src.api.v1.endpoints.code_rules.save_code_rule',
    summary: `Save code rule ${name} (${'ab'.repeat(6)}...)`,
    settings_change: false,
    payload: {
      body: { name },
      facts: {
        name, code_sha256: `${id.slice(4).padEnd(12, '0')}${'f'.repeat(52)}`, functions: ['digit_sum', 'loud'],
        case_results: cases.map((c) => ({ ...c, expected: {}, result: {} })),
        previous: previous === null ? null : { version: previous, code_sha256: 'e'.repeat(64) },
      },
    },
  });
const GAMMA = rule('apr_gamma', 'gamma-digits', null, [
  { id: 'c1', function: 'digit_sum', passed: true },
  { id: 'c2', function: 'loud', passed: true },
]);
const DELTA = rule('apr_delta', 'delta-loud', 2, [{ id: 'd1', function: 'loud', passed: false }]);

describe('Approve all code rules', () => {
  it('is its own button, from two waiting code rules, beside the settings one and never counting it', async () => {
    server([LOG, JUDGE, GAMMA, DELTA, PUSH]);
    const waiting = await openApprovals();
    expect(await waiting.findByRole('button', { name: 'Approve all code rules (2)' })).toBeInTheDocument();
    expect(waiting.getByRole('button', { name: 'Approve all settings changes (2)' })).toBeInTheDocument();
  });

  it('is on the banner too', async () => {
    server([GAMMA, DELTA]);
    window.history.replaceState(null, '', '#/datasets');
    render(<App />);
    const banner = within(await screen.findByTestId('approvals-banner'));
    expect(await banner.findByRole('button', { name: 'Approve all code rules (2)' })).toBeInTheDocument();
  });

  it('is not shown for ONE waiting code rule', async () => {
    server([GAMMA, LOG]);
    const waiting = await openApprovals();
    await waiting.findAllByTestId('approval-row');
    expect(screen.queryByTestId('approve-all-code-rules-button')).toBeNull();
  });

  it('lists name and version, the code hash, the functions and the case results, then approves only the code rules in ONE call', async () => {
    const { calls } = server([LOG, JUDGE, GAMMA, DELTA]);
    const waiting = await openApprovals();
    fireEvent.click(await waiting.findByTestId('approve-all-code-rules-button'));
    const confirm = within(screen.getByRole('dialog', { name: 'Approve all code rules' }));
    const heads = confirm.getAllByTestId('approve-all-code-rule-head').map((el) => el.textContent);
    expect(heads).toEqual(['gamma-digits, version 1 (new), code gamma0000000', 'delta-loud, version 3 (after 2), code delta0000000']);
    expect(confirm.getAllByText('Functions: digit_sum, loud')).toHaveLength(2);
    const cases = confirm.getAllByTestId('approve-all-cases').map((el) => el.textContent);
    expect(cases[0]).toContain('Cases at filing: 2 of 2 passed');
    expect(cases[0]).toContain('c1 (digit_sum): passed');
    expect(cases[1]).toContain('d1 (loud): FAILED');
    expect(confirm.queryByTestId('approve-all-change')).toBeNull();

    fireEvent.click(confirm.getByTestId('approve-all-code-rules-confirm-button'));
    expect(await screen.findByTestId('approve-all-code-rules-outcome')).toHaveTextContent('Approved 2 of 2 code rules.');
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { method: 'POST', path: BATCH, body: { kind: 'code_rules', approval_ids: ['apr_gamma', 'apr_delta'] } },
    ]);
    expect(screen.queryByTestId('approve-all-settings-outcome')).toBeNull();
  });

  it('describeCodeRule shows text as text and survives missing facts', () => {
    expect(describeCodeRule(base('apr_bare', { action: 'code_rule_save', summary: 'Save code rule x' }))).toEqual({
      name: 'Save code rule x', version: 'version 1 (new)', sha12: 'unknown', functions: 'none listed', cases: [],
    });
  });
});

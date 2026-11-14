// 018 FTASKS 16.3 and 16.4: the Code rules screen. Reachability from the live registry (remove the
// panels.ts entry or the panelComponents line and this goes red), Revoke as ONE POST carrying the
// reason, Approve again's two honest refusals (409 code_rule_case_failed, 503 runner_unavailable),
// the Settings PIN asked for on 403 PIN_REQUIRED, honest states never green, times in US Eastern.
// Everything runs through the real store and API client against a stubbed fetch.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { registerPinPrompter } from '@/api/pinSession';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { getPanel, PANELS } from '@/config/panels';
import { useCodeRulesStore } from '@/stores/codeRulesStore';
import { useHealthStore } from '@/stores/healthStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';

import { CodeRulesPanel } from './CodeRulesPanel';

const SHA = 'ef56'.repeat(16);
const LIST_PATH = '/api/v1/code-rules';
const DETAIL_PATH = '/api/v1/code-rules/digits/versions/2';
const REVOKE = `POST /api/v1/code-rules/approvals/${SHA}/revoke`;
const APPROVE = `POST /api/v1/code-rules/approvals/${SHA}/approve`;

const summary = (state: string, contract: unknown = null) => ({
  name: 'digits', behaviour_key: 'fixture-gamma', version: 2, code_sha256: SHA, approval_state: state,
  functions: ['count_digits'], last_case_result: contract, archived: false,
});

const detail = (state: string, history: unknown[] = [], contract: unknown = null) => ({
  name: 'digits', version: 2, behaviour_key: 'fixture-gamma', code_sha256: SHA, runner_contract: '1.0',
  document: { source: 'def count_digits(text, params):\n    return 0\n', functions: [{ name: 'count_digits' }], params_schema: { type: 'object' } },
  cases: [{ id: 'none', function: 'count_digits', text: 'abc', params: {}, expect: { count: 0 } }],
  approval: { state, pending_approvals: [], history, history_rows: history.length },
  contract_status: contract, superseded_by: null, archived: false, created_by: 'agent:dataworks-mcp',
  created_by_origin: 'agent', created_at: '2026-10-24T18:05:00+00:00', used_by: { recorded: true, versions: [] },
});

const APPROVED = { id: 1, action: 'approve', reason: 'read it', changed_by: 'Sean', approval_id: 'apr_1', created_at: '2026-10-24T18:05:00+00:00' };
const REVOKED = { id: 2, action: 'revoke', reason: 'leaks a pattern', changed_by: 'Sean', approval_id: null, created_at: '2026-10-24T19:10:00+00:00' };

const routes = (state: string, extra: Record<string, unknown> = {}) => ({
  [`GET ${LIST_PATH}`]: { items: [summary(state)], total: 1 },
  [`GET ${DETAIL_PATH}`]: detail(state, state === 'approved' ? [APPROVED] : [REVOKED, APPROVED]),
  'GET /api/v1/runner/contract': { contract_version: '1.0', code_rules_enabled: true },
  ...extra,
});

async function openDigits() {
  render(<CodeRulesPanel panel={getPanel('code-rules')} />);
  fireEvent.click(await screen.findByRole('button', { name: 'digits' }));
  await screen.findByTestId('code-rule-detail');
}

function reset() {
  vi.unstubAllGlobals();
  useCodeRulesStore.setState({ items: [], current: null, error: null, refusal: null, notice: null, lastCaseResults: null, enabled: null });
  useHealthStore.setState({ health: null });
  window.location.hash = '';
}

describe('Code rules is a registered screen', () => {
  afterEach(reset);

  it('is in PANELS after Operators and maps to CodeRulesPanel', () => {
    const ids = PANELS.map((p) => p.id) as string[];
    expect(ids).toContain('code-rules');
    expect(ids.indexOf('code-rules')).toBe(ids.indexOf('operators') + 1);
    expect(PANEL_COMPONENTS['code-rules']).toBe(CodeRulesPanel);
  });

  it('the shell renders it at #/code-rules', async () => {
    mockFetch({
      'GET /api/health': HEALTH,
      'GET /api/v1/approvals': { approvals: [] },
      'GET /api/v1/jobs': { jobs: [] },
      [`GET ${LIST_PATH}`]: { items: [], total: 0 },
      'GET /api/v1/runner/contract': { contract_version: '1.0', code_rules_enabled: true },
    });
    window.location.hash = '#/code-rules';
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('code-rules-empty')).toBeInTheDocument());
    expect(screen.getByTestId('code-rules-count-only').textContent).toContain('can only report a count');
  });
});

describe('Revoke', () => {
  afterEach(reset);

  it('sends ONE POST with the reason, then reads the rule again', async () => {
    const { calls } = mockFetch(
      routes('approved', { [REVOKE]: { code_sha256: SHA, state: 'revoked', changed: true, name: 'digits', version: 2 } }),
    );
    await openDigits();
    expect(screen.queryByTestId('code-rule-approve-again')).toBeNull();
    fireEvent.click(screen.getByTestId('code-rule-revoke'));
    const button = screen.getByRole('button', { name: 'Revoke this code' });
    expect(button).toBeDisabled(); // a reason is required
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: '  leaks a pattern  ' } });
    fireEvent.click(button);
    await screen.findByTestId('code-rules-notice');
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([{ method: 'POST', path: `/api/v1/code-rules/approvals/${SHA}/revoke`, body: { reason: 'leaks a pattern' } }]);
    expect(calls.filter((c) => c.path === DETAIL_PATH).length).toBe(2);
  });

  it('asks for the Settings PIN on 403 PIN_REQUIRED and sends it once more', async () => {
    let n = 0;
    const { calls } = mockFetch(
      routes('approved', {
        [REVOKE]: () =>
          ++n === 1
            ? { status: 403, json: { error: { code: 'PIN_REQUIRED', message: 'Settings are locked with a PIN.', details: {} } } }
            : { json: { code_sha256: SHA, state: 'revoked', changed: true, name: 'digits', version: 2 } },
        'POST /api/v1/settings/pin/unlock': { token: 't' },
      }),
    );
    const prompter = vi.fn(async () => true);
    const unregister = registerPinPrompter(prompter);
    try {
      await openDigits();
      fireEvent.click(screen.getByTestId('code-rule-revoke'));
      fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'done with it' } });
      fireEvent.click(screen.getByRole('button', { name: 'Revoke this code' }));
      await screen.findByTestId('code-rules-notice');
      expect(prompter).toHaveBeenCalledTimes(1);
      expect(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/revoke')).length).toBe(2);
    } finally {
      unregister();
    }
  });
});

describe('Approve again', () => {
  afterEach(reset);

  async function approveWith(answer: unknown) {
    const { calls } = mockFetch(routes('revoked', { [APPROVE]: () => answer }));
    await openDigits();
    expect(screen.queryByTestId('code-rule-revoke')).toBeNull();
    expect(screen.getByTestId('code-rule-state').dataset.state).toBe('revoked');
    expect(screen.getByTestId('code-rule-state').textContent).toContain('Oct 24, 2026, 3:10 PM ET');
    fireEvent.click(screen.getByTestId('code-rule-approve-again'));
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'fixed upstream' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve this code again' }));
    return calls;
  }

  it('a failing case (409 code_rule_case_failed) says which case and that nothing changed', async () => {
    const calls = await approveWith({
      status: 409,
      json: { error: { code: 'code_rule_case_failed', message: 'Case none failed.', details: { case: 'none', expected: { count: 0 }, actual: { reason: 'rule_error' }, contract_version: '1.1' } } },
    });
    const refusal = await screen.findByTestId('code-rule-refusal');
    expect(refusal.textContent).toBe('Not approved: case none expected count 0 and got reason rule_error under runner contract 1.1. Nothing changed: the code is still not approved.');
    expect(refusal.className).not.toMatch(/green/);
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { method: 'POST', path: `/api/v1/code-rules/approvals/${SHA}/approve`, body: { reason: 'fixed upstream' } },
    ]);
    expect(screen.queryByTestId('code-rules-notice')).toBeNull();
  });

  it('an unreachable runner (503) says the cases could not run, nothing changed', async () => {
    await approveWith({ status: 503, json: { error: { code: 'runner_unavailable', message: 'The code-rule runner cannot be reached.', details: {} } } });
    const refusal = await screen.findByTestId('code-rule-refusal');
    expect(refusal.dataset.code).toBe('runner_unavailable');
    expect(refusal.textContent).toContain('The runner is unavailable');
    expect(refusal.textContent).toContain('Nothing changed.');
  });

  it('success shows the cases it ran', async () => {
    await approveWith({
      json: { code_sha256: SHA, state: 'approved', changed: true, name: 'digits', version: 2, case_results: [{ id: 'none', function: 'count_digits', expected: { count: 0 }, result: { count: 0 }, passed: true }] },
    });
    expect((await screen.findByTestId('code-rules-notice')).textContent).toContain('Approved digits version 2 again');
    expect(screen.getByTestId('code-rule-recheck').textContent).toContain('none');
  });
});

describe('honest states', () => {
  afterEach(reset);

  it('"not recorded" and "failing under contract" and "runner unavailable" are never green, both colour modes', async () => {
    const failing = { contract_version: '1.1', bundle_digest: 'd', passed: false, failing_case: 'none', reason: 'rule_error', run_at: '2026-10-24T18:05:00+00:00' };
    mockFetch({
      [`GET ${LIST_PATH}`]: { items: [summary('unknown', failing)], total: 1 },
      [`GET ${DETAIL_PATH}`]: detail('unknown', [], null),
      'GET /api/v1/runner/contract': { contract_version: '1.1', code_rules_enabled: false },
    });
    useHealthStore.setState({ health: { ...HEALTH, dependencies: { ...HEALTH.dependencies, runner: { ok: false, reason: 'connection refused' } } } as never });
    await openDigits();
    const states = Array.from(document.querySelectorAll('[data-state]')) as HTMLElement[];
    const byState = Object.fromEntries(states.map((el) => [el.dataset.state, el]));
    expect(byState['failing'].textContent).toContain('Failing under contract 1.1: case none (rule_error), Oct 24, 2026, 2:05 PM ET');
    expect(byState['not-recorded'].textContent).toContain('not recorded');
    expect(byState['runner-unavailable'].textContent).toBe('Runner unavailable: connection refused');
    expect(byState['disabled'].textContent).toContain('switched off');
    expect(byState['unknown'].textContent).toContain('Never approved');
    // a never-approved hash offers Approve again, not Revoke (control S5-M12)
    expect(screen.getByTestId('code-rule-approve-again')).toBeInTheDocument();
    expect(screen.queryByTestId('code-rule-revoke')).toBeNull();
    for (const key of ['failing', 'not-recorded', 'runner-unavailable', 'disabled', 'unknown']) {
      expect(byState[key].className, key).not.toMatch(/green/);
      expect(byState[key].className, key).toMatch(/dark:text-/);
    }
    expect(screen.getByText('Not recorded: this code has never been approved or revoked.')).toBeInTheDocument();
  });
});

// 018 FTASKS 16.2: the code-rule approval card, rendered through the real RequestRow (the banner and
// the Approvals screen), so removing the `code_rule_save` branch from RequestRow turns this red.
// The fixture's facts are shaped as save.approval_facts writes them, and the source comes from
// payload.body.document.source, where the agent's document is stored.
import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ACTION_LABELS } from '@/components/settings/AgentAccessCard';
import { mockFetch } from '@/test/fetchMock';
import type { Approval } from '@/types/api';

import { RequestRow } from './ApprovalsBanner';

const SHA = 'ab12'.repeat(16);
const PREV = 'cd34'.repeat(16);
const SOURCE = 'import re\n\ndef count_digits(text, params):\n    # digits only\n    return len(re.findall(r"\\d", text))\n';

const request = (facts: Record<string, unknown>, withSource = true): Approval => ({
  id: 'apr_cr',
  action: 'code_rule_save',
  target: 'digits',
  summary: `Save code rule digits (${SHA.slice(0, 12)}...)`,
  payload: {
    body: { name: 'digits', behaviour_key: 'fixture-gamma', document: withSource ? { source: SOURCE, functions: [{ name: 'count_digits' }] } : {}, cases: [] },
    facts,
  },
  request_digest: 'x',
  requested_by: 'agent:dataworks-mcp',
  status: 'pending',
  expires_at: new Date(Date.now() + 3600_000).toISOString(),
  decided_by: null,
  created_at: new Date().toISOString(),
});

const FACTS = {
  name: 'digits',
  behaviour_key: 'fixture-gamma',
  code_sha256: SHA,
  approval_state: 'unknown',
  last_decision: null,
  imports: ['re'],
  functions: ['count_digits'],
  params_schema: { type: 'object', properties: { min: { type: 'integer' } } },
  case_results: [
    { id: 'has-digits', function: 'count_digits', expected: { count: 3 }, result: { count: 3 }, passed: true },
    { id: 'no-digits', function: 'count_digits', expected: { count: 0 }, result: { count: 0 }, passed: true },
  ],
  contract_version: '1.0',
  previous: { version: 1, code_sha256: PREV },
  diff: '--- previous\n+++ this\n@@ -1,2 +1,2 @@\n-    return 0\n+    return len(re.findall(r"\\d", text))\n',
};

describe('CodeRuleCard in RequestRow (code_rule_save)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('names the action and shows source, diff, functions, schema, case results, imports and hash', () => {
    expect(ACTION_LABELS.code_rule_save).toBe('Code rule save');
    render(<ul><RequestRow request={request(FACTS)} now={Date.now()} /></ul>);
    const card = screen.getByTestId('code-rule-card');
    expect(screen.getByRole('button', { name: 'Approve code rule save' })).toBeInTheDocument();
    expect(within(card).getByTestId('code-rule-source').textContent).toBe(SOURCE);
    expect(within(card).getByTestId('code-rule-source').querySelector('[data-token="keyword"]')?.textContent).toBe('import');
    const diff = within(card).getByTestId('code-rule-diff');
    expect(diff.querySelector('[data-diff="add"]')?.textContent).toContain('return len(');
    expect(diff.querySelector('[data-diff="remove"]')?.textContent).toBe('-    return 0');
    expect(card.textContent).toContain('Against version 1');
    expect(within(card).getByTestId('code-rule-functions').textContent).toBe('count_digits');
    expect(JSON.parse(within(card).getByTestId('code-rule-params-schema').textContent ?? '')).toEqual(FACTS.params_schema);
    const cases = within(card).getByTestId('code-rule-case-results');
    expect(cases.textContent).toContain('has-digits');
    expect(cases.textContent).toContain('count 3');
    expect(cases.textContent).toContain('no-digits');
    expect(within(card).getByTestId('code-rule-imports').textContent).toBe('re');
    expect(within(card).getByTestId('code-rule-hash').textContent).toBe(SHA);
    expect(within(card).getByTestId('code-rule-state').dataset.state).toBe('unknown');
    expect(within(card).getByTestId('code-rule-state').className).not.toMatch(/green/);
  });

  it('puts a revocation first, read NOW, with who, when (US Eastern) and why, never green; says Approve is refused', async () => {
    // The facts are a snapshot from when the agent asked (a revoked hash is never queued, D60): the
    // hash was revoked while the request waited. Only the fresh read can show it.
    const revoke = { id: 7, action: 'revoke', reason: 'leaks a pattern', changed_by: 'Sean', approval_id: null, created_at: '2026-10-24T18:05:00+00:00' };
    const { calls } = mockFetch({ 'GET /api/v1/code-rules/approvals': { code_sha256: SHA, state: 'revoked', history: [revoke] } });
    render(<ul><RequestRow request={request(FACTS)} now={Date.now()} /></ul>);
    await waitFor(() => expect(screen.getByTestId('code-rule-state').dataset.state).toBe('revoked'));
    expect(calls.filter((c) => c.path === `/api/v1/code-rules/approvals?code_sha256=${SHA}`).length).toBe(1);
    const state = screen.getByTestId('code-rule-state');
    expect(state.textContent).toContain('Revoked');
    expect(state.textContent).toContain('by Sean, Oct 24, 2026, 2:05 PM ET');
    expect(state.textContent).toContain('(leaks a pattern)');
    expect(state.textContent).toContain('Approving this request will be refused');
    expect(state.textContent).toContain('Approve again on the Code rules screen');
    expect(state.textContent).not.toContain('undoes');
    expect(state.className).not.toMatch(/green/);
    expect(state.className).toMatch(/dark:/);
    expect(screen.queryByTestId('code-rule-state-age')).toBeNull();
    // the state sits above the source
    const card = screen.getByTestId('code-rule-card');
    expect(card.firstElementChild).toBe(state);
  });

  it('when the current state cannot be read, shows the snapshot and says it is the state when the agent asked', async () => {
    mockFetch({ 'GET /api/v1/code-rules/approvals': () => ({ status: 503, json: { error: { code: 'X', message: 'down', details: {} } } }) });
    render(<ul><RequestRow request={request(FACTS)} now={Date.now()} /></ul>);
    await waitFor(() => expect(screen.getByTestId('code-rule-state-age').dataset.live).toBe('error'));
    expect(screen.getByTestId('code-rule-state-age').textContent).toContain('could not be read');
    expect(screen.getByTestId('code-rule-state').dataset.state).toBe('unknown');
  });

  it('says so when there is no previous version, and when the source is missing', () => {
    render(<ul><RequestRow request={request({ ...FACTS, previous: null, diff: '' }, false)} now={Date.now()} /></ul>);
    expect(screen.getByTestId('code-rule-diff-none').textContent).toContain('first version');
    expect(screen.queryByTestId('code-rule-source')).toBeNull();
    expect(screen.getByTestId('code-rule-card').textContent).toContain('does not carry the source');
  });

  it('a failed case reads "failed", not green', () => {
    const facts = { ...FACTS, case_results: [{ id: 'bad', function: 'count_digits', expected: { count: 1 }, result: { reason: 'rule_error' }, passed: false }] };
    render(<ul><RequestRow request={request(facts)} now={Date.now()} /></ul>);
    const cell = screen.getByText('failed');
    expect(cell.className).toMatch(/red/);
    expect(screen.getByTestId('code-rule-case-results').textContent).toContain('reason rule_error');
  });

  it('renders hostile text in the diff, names, case ids, imports and reasons as characters, never as HTML', async () => {
    const evil = '<img src=x onerror="window.__pwned=1">';
    const revoke = { id: 9, action: 'revoke', reason: evil, changed_by: evil, approval_id: null, created_at: null };
    mockFetch({ 'GET /api/v1/code-rules/approvals': { code_sha256: SHA, state: 'revoked', history: [revoke] } });
    const facts = {
      ...FACTS,
      name: evil,
      behaviour_key: evil,
      imports: [evil],
      functions: [evil],
      diff: `--- previous\n+++ this\n@@ -1 +1 @@\n-${evil}\n+${evil}\n`,
      case_results: [{ id: evil, function: evil, expected: { count: 1 }, result: { reason: evil }, passed: false }],
    };
    render(<ul><RequestRow request={request(facts)} now={Date.now()} /></ul>);
    await waitFor(() => expect(screen.getByTestId('code-rule-state').dataset.state).toBe('revoked'));
    const card = screen.getByTestId('code-rule-card');
    expect(card.querySelector('img')).toBeNull();
    expect(screen.getByTestId('code-rule-diff').textContent).toContain(evil);
    expect(screen.getByTestId('code-rule-state').textContent).toContain(evil);
    expect(screen.getByTestId('code-rule-case-results').textContent).toContain(evil);
    expect(screen.getByTestId('code-rule-imports').textContent).toBe(evil);
  });

  it('other actions get no code-rule card', () => {
    render(<ul><RequestRow request={{ ...request(FACTS), action: 'hub_push' }} now={Date.now()} /></ul>);
    expect(screen.queryByTestId('code-rule-card')).toBeNull();
  });
});

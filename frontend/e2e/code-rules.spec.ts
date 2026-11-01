import { expect, test, type Page } from '@playwright/test';

import { HEALTH as HEALTH_WITH_RUNNER, stubApi } from './fixtures';

// Feature 018 FTASKS 16.7 (by hand, not CI): the code-rule approval card and the Code rules screen
// in light and dark mode, against the built app with route fixtures shaped as the backend answers
// (services/code_rules/save.approval_facts, views.py, endpoints/code_rules.py).
//  - The card reads the hash's CURRENT state (the facts are a queue-time snapshot): here the hash was
//    revoked while the request waited, so the card says approving will be refused (D60), in red.
//  - The source, diff, functions, case ids and imports render as characters, never as HTML.
//  - Revoke on the screen is one decision: the first POST is refused 403 PIN_REQUIRED, the Settings
//    PIN dialog asks once, and the same POST (same reason) is sent once more with the PIN session.
//  - Approve again refused 409 code_rule_case_failed says which case and that nothing changed.
const SHA = 'ab12'.repeat(16);
const PREV = 'cd34'.repeat(16);
const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const SOURCE = `import re\n\n# ${HOSTILE}\ndef count_digits(text, params):\n    return len(re.findall(r"\\d", text))\n`;
const REVOKED = { id: 2, action: 'revoke', reason: 'leaks a pattern', changed_by: 'Sean', approval_id: null, created_at: '2026-10-24T19:10:00+00:00' };
const APPROVED = { id: 1, action: 'approve', reason: 'read it', changed_by: 'Sean', approval_id: 'apr_1', created_at: '2026-10-24T18:05:00+00:00' };

const PENDING = {
  id: 'apr_cr', action: 'code_rule_save', target: 'digits',
  summary: `Save code rule digits (${SHA.slice(0, 12)}...)`,
  payload: {
    body: { name: 'digits', behaviour_key: 'fixture-gamma', document: { source: SOURCE, functions: [{ name: 'count_digits' }] }, cases: [] },
    facts: {
      name: 'digits', behaviour_key: 'fixture-gamma', code_sha256: SHA, approval_state: 'approved', last_decision: APPROVED,
      imports: ['re'], functions: ['count_digits'], params_schema: { type: 'object' }, contract_version: '1.0',
      case_results: [
        { id: 'has-digits', function: 'count_digits', expected: { count: 3 }, result: { count: 3 }, passed: true },
        { id: HOSTILE, function: 'count_digits', expected: { count: 0 }, result: { reason: 'operator_code_raised' }, passed: false },
      ],
      previous: { version: 1, code_sha256: PREV },
      diff: `--- previous\n+++ this\n@@ -1,2 +1,2 @@\n-    return 0\n+    return len(re.findall(r"\\d", text))\n+# ${HOSTILE}\n`,
    },
  },
  request_digest: 'abc', requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 23 * 3600_000).toISOString(), decided_by: null, created_at: new Date().toISOString(),
};

const summary = (state: string) => ({
  name: 'digits', behaviour_key: 'fixture-gamma', version: 2, code_sha256: SHA, approval_state: state,
  functions: ['count_digits'], archived: false,
  last_case_result: { contract_version: '1.0', bundle_digest: 'd', passed: true, failing_case: null, reason: null, run_at: '2026-10-24T18:04:00+00:00' },
});
const detail = (state: string, history: unknown[]) => ({
  name: 'digits', version: 2, behaviour_key: 'fixture-gamma', code_sha256: SHA, runner_contract: '1.0',
  document: { source: SOURCE, functions: [{ name: 'count_digits' }], params_schema: { type: 'object' } },
  cases: [{ id: 'has-digits', function: 'count_digits', text: 'a1b2c3', params: {}, expect: { count: 3 } }],
  approval: { state, pending_approvals: [], history, history_rows: history.length },
  contract_status: summary(state).last_case_result, superseded_by: null, archived: false,
  created_by: 'agent:dataworks-mcp', created_by_origin: 'agent', created_at: '2026-10-24T18:05:00+00:00',
  used_by: { recorded: true, versions: [] },
});

async function useMode(page: Page, mode: 'dark' | 'light') {
  await page.addInitScript((theme) => {
    window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
  }, mode);
}

/** The colour channel that dominates a CSS colour: a "red" note must not read green, and back. */
const hue = (rgb: string) => {
  const [r, g, b] = (rgb.match(/[\d.]+/g) ?? []).map(Number);
  return g > r && g > b ? 'green' : r > g && r > b ? 'red' : 'other';
};

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test('the Approvals card shows the current revoked state, the code as text, and approves once', async ({ page }) => {
      await stubApi(page, {
        health: { ...HEALTH_WITH_RUNNER, dependencies: { ...HEALTH_WITH_RUNNER.dependencies, runner: { ok: true, reason: null } } },
      });
      await useMode(page, mode);
      let approved = 0;
      let stateReads = 0;
      await page.route('**/api/v1/approvals**', async (route) => {
        const url = new URL(route.request().url());
        if (route.request().method() === 'POST' && url.pathname === '/api/v1/approvals/apr_cr/approve') {
          approved += 1;
          // D60: the replay re-checks the hash and refuses a revoked one.
          return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'code_rule_revoked', message: 'This exact code was revoked.', details: {} } }) });
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approvals: url.searchParams.get('status') === 'pending' || !url.search ? [PENDING] : [] }) });
      });
      await page.route('**/api/v1/code-rules/approvals?code_sha256=*', async (route) => {
        stateReads += 1;
        expect(new URL(route.request().url()).searchParams.get('code_sha256')).toBe(SHA);
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code_sha256: SHA, state: 'revoked', history: [REVOKED, APPROVED] }) });
      });

      await page.goto('/#/approvals');
      await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
      await expect(page.locator('html')).toHaveClass(mode === 'dark' ? /dark/ : /^(?!.*dark).*$/);
      const card = page.getByTestId('code-rule-card');
      await expect(card).toHaveCount(1);

      // The fresh read replaces the queue-time "approved" snapshot.
      const state = card.getByTestId('code-rule-state');
      await expect(state).toHaveAttribute('data-state', 'revoked');
      await expect(state).toContainText('revoked by Sean');
      await expect(state).toContainText('(leaks a pattern)');
      await expect(state).toContainText('Approving this request will be refused');
      await expect(card.getByTestId('code-rule-state-snapshot')).toHaveCount(0);
      expect(hue(await state.evaluate((el) => getComputedStyle(el).color))).toBe('red');
      expect(stateReads).toBeGreaterThanOrEqual(1);

      // Everything the hash covers, as characters.
      await expect(card.getByTestId('code-rule-hash')).toHaveText(SHA);
      await expect(card.getByTestId('code-rule-source')).toContainText(HOSTILE);
      await expect(card.getByTestId('code-rule-source').locator('[data-token="keyword"]').first()).toHaveText('import');
      await expect(card.getByTestId('code-rule-diff').locator('[data-diff="add"]')).toHaveCount(2);
      await expect(card.getByTestId('code-rule-diff').locator('[data-diff="remove"]')).toHaveCount(1);
      await expect(card.getByTestId('code-rule-functions')).toHaveText('count_digits');
      await expect(card.getByTestId('code-rule-imports')).toHaveText('re');
      await expect(card.getByTestId('code-rule-case-results')).toContainText('reason operator_code_raised');
      await expect(card.getByTestId('code-rule-case-results')).toContainText(HOSTILE);
      await expect(card.locator('img')).toHaveCount(0);
      expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();

      const approve = page.getByRole('button', { name: 'Approve code rule save' });
      await approve.focus();
      expect(await approve.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
      await page.screenshot({ path: `e2e/captures/${mode}-code-rule-card.png`, fullPage: true });
      await approve.click();
      await expect.poll(() => approved).toBe(1);
      await page.waitForTimeout(300);
      expect(approved).toBe(1);
    });

    test('the Code rules screen: Revoke asks for the PIN once, then Approve again refused names the case', async ({ page }) => {
      await stubApi(page, {
        health: { ...HEALTH_WITH_RUNNER, dependencies: { ...HEALTH_WITH_RUNNER.dependencies, runner: { ok: true, reason: null } } },
      });
      await useMode(page, mode);
      let state = 'approved';
      const revokes: Array<{ body: unknown; pinHeader: string | undefined }> = [];
      const approves: unknown[] = [];
      let unlocks = 0;
      await page.route('**/api/v1/runner/contract', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ contract_version: '1.0', code_rules_enabled: true }) }));
      await page.route('**/api/v1/settings/pin/session', async (route) => {
        unlocks += 1;
        expect(route.request().postDataJSON()).toEqual({ pin: '2468' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ token: 'pin-session-1', expires_at: new Date(Date.now() + 600_000).toISOString() }) });
      });
      await page.route('**/api/v1/code-rules**', async (route) => {
        const req = route.request();
        const path = new URL(req.url()).pathname;
        const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
        if (req.method() === 'GET' && path === '/api/v1/code-rules') return json({ items: [summary(state)], total: 1 });
        if (req.method() === 'GET' && path === '/api/v1/code-rules/digits/versions/2') {
          return json(detail(state, state === 'approved' ? [APPROVED] : [{ ...REVOKED, reason: 'found a leak' }, APPROVED]));
        }
        if (req.method() === 'POST' && path === `/api/v1/code-rules/approvals/${SHA}/revoke`) {
          const pinHeader = req.headers()['x-settings-pin-session'];
          revokes.push({ body: req.postDataJSON(), pinHeader });
          if (!pinHeader) return json({ error: { code: 'PIN_REQUIRED', message: 'Settings are locked with a PIN.', details: {} } }, 403);
          state = 'revoked';
          return json({ code_sha256: SHA, state: 'revoked', changed: true, name: 'digits', version: 2 });
        }
        if (req.method() === 'POST' && path === `/api/v1/code-rules/approvals/${SHA}/approve`) {
          approves.push(req.postDataJSON());
          return json({ error: { code: 'code_rule_case_failed', message: 'A stored case failed.', details: { case: 'has-digits', expected: { count: 3 }, actual: { count: 2 }, contract_version: '1.1' } } }, 409);
        }
        return json({ error: { code: 'NOT_FOUND', message: 'not stubbed', details: {} } }, 404);
      });

      await page.goto('/#/code-rules');
      await expect(page.getByRole('heading', { level: 1, name: 'Code rules' })).toBeVisible();
      await expect(page.locator('html')).toHaveClass(mode === 'dark' ? /dark/ : /^(?!.*dark).*$/);
      await expect(page.getByTestId('code-rules-count-only')).toContainText('A code rule can only report a count');
      await expect(page.getByTestId('code-rules-runtime').locator('[data-state="runner-ok"]')).toHaveText('Runner: reachable');
      await expect(page.getByTestId('code-rules-runtime').locator('[data-state="disabled"]')).toHaveCount(0);
      // The sidebar entry opens it too (the panel registry, not only the route).
      await expect(page.getByTestId('sidebar').getByRole('link', { name: 'Code rules' }).or(page.getByTestId('sidebar').getByRole('button', { name: 'Code rules' })).first()).toBeVisible();

      await page.getByRole('button', { name: 'digits' }).click();
      const detailCard = page.getByTestId('code-rule-detail');
      await expect(detailCard.getByTestId('code-rule-state')).toHaveAttribute('data-state', 'approved');
      await expect(detailCard.getByTestId('contract-status')).toHaveAttribute('data-state', 'passed');
      await expect(detailCard.getByTestId('code-rule-detail-source')).toContainText(HOSTILE);
      await expect(detailCard.locator('img')).toHaveCount(0);
      await expect(detailCard).toContainText('PM ET');
      await page.screenshot({ path: `e2e/captures/${mode}-code-rules-approved.png`, fullPage: true });

      // Revoke: a reason is required; the PIN is asked for once; the same POST goes again.
      await detailCard.getByTestId('code-rule-revoke').click();
      const confirm = page.getByRole('button', { name: 'Revoke this code' });
      await expect(confirm).toBeDisabled();
      await page.getByLabel('Reason (kept in this code\'s approval history)').fill('found a leak');
      await confirm.click();
      const pinInput = page.locator('#settings-pin-input');
      await expect(pinInput).toBeVisible();
      await pinInput.fill('2468');
      await page.getByTestId('pin-unlock').click();
      await expect(page.getByTestId('code-rules-notice')).toContainText('Revoked digits version 2');
      expect(unlocks).toBe(1);
      expect(revokes).toEqual([
        { body: { reason: 'found a leak' }, pinHeader: undefined },
        { body: { reason: 'found a leak' }, pinHeader: 'pin-session-1' },
      ]);
      const revokedNote = detailCard.getByTestId('code-rule-state');
      await expect(revokedNote).toHaveAttribute('data-state', 'revoked');
      await expect(revokedNote).toContainText('(found a leak)');
      await expect(revokedNote).toContainText('Only Approve again restores it');
      expect(hue(await revokedNote.evaluate((el) => getComputedStyle(el).color))).toBe('red');
      await expect(page.getByTestId('code-rules-list').getByTestId('code-rule-row')).toContainText('revoked');
      await expect(detailCard.getByTestId('code-rule-revoke')).toHaveCount(0);
      await page.screenshot({ path: `e2e/captures/${mode}-code-rules-revoked.png`, fullPage: true });

      // Approve again: the stored case fails under the current contract; nothing changes.
      await detailCard.getByTestId('code-rule-approve-again').click();
      await page.getByLabel('Reason (kept in this code\'s approval history)').fill('fixed upstream');
      await page.getByRole('button', { name: 'Approve this code again' }).click();
      const refusal = page.getByTestId('code-rule-refusal');
      await expect(refusal).toHaveAttribute('data-code', 'code_rule_case_failed');
      await expect(refusal).toContainText('case has-digits expected count 3 and got count 2 under runner contract 1.1');
      await expect(refusal).toContainText('Nothing changed');
      expect(hue(await refusal.evaluate((el) => getComputedStyle(el).color))).toBe('red');
      expect(approves).toEqual([{ reason: 'fixed upstream' }]);
      await expect(detailCard.getByTestId('code-rule-state')).toHaveAttribute('data-state', 'revoked');
      await page.screenshot({ path: `e2e/captures/${mode}-code-rules-refused.png`, fullPage: true });
    });

    test('with code rules switched off (the default after this deploy) the screen says so', async ({ page }) => {
      await stubApi(page);
      await useMode(page, mode);
      await page.goto('/#/code-rules');
      await expect(page.getByTestId('code-rules-runtime').locator('[data-state="disabled"]')).toContainText('Code rules are switched off in this deployment');
      await expect(page.getByTestId('code-rules-runtime').locator('[data-state="runner-not-recorded"]')).toHaveText('Runner: not recorded');
      await expect(page.getByTestId('code-rules-empty')).toBeVisible();
      await page.screenshot({ path: `e2e/captures/${mode}-code-rules-off.png`, fullPage: true });
    });
  });
}

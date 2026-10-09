import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';

// Feature 010 (FTASKS 11.6): an agent's publish waits in the banner with its identity; approving
// runs it once; the Agent access card is display only. Both colour modes, focus visible.
const PENDING = {
  id: 'apr_pub1', action: 'hub_push', target: 'src.api.v1.endpoints.publishing.create_publish',
  summary: 'Publish ver_1 to owner/humor (private)', payload: {}, request_digest: 'abc',
  requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 23 * 3600_000).toISOString(), decided_by: null,
  created_at: new Date().toISOString(),
};

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page);
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    test('an agent publish waits in the banner with its identity, and approve runs it once', async ({ page }) => {
      let approved = 0;
      await page.route('**/api/v1/approvals**', async (route) => {
        const url = new URL(route.request().url());
        if (route.request().method() === 'POST' && url.pathname === '/api/v1/approvals/apr_pub1/approve') {
          approved += 1;
          return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...PENDING, status: 'executed' }) });
        }
        const approvals = approved === 0 ? [PENDING] : [];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approvals }) });
      });
      await page.goto('/#/datasets');
      const banner = page.getByTestId('approvals-banner');
      await expect(banner).toContainText('1 agent request waiting for your approval');
      await expect(banner.getByTestId('who-agent')).toContainText('dataworks-mcp');
      await expect(banner).toContainText('Hub push');
      await expect(banner).toContainText('Publish ver_1 to owner/humor (private)');
      await expect(banner).toContainText('expires in 23 h');
      const approve = banner.getByRole('button', { name: 'Approve hub push' });
      await approve.focus();
      const shadow = await approve.evaluate((el) => getComputedStyle(el).boxShadow);
      expect(shadow).not.toBe('none');
      await page.screenshot({ path: `e2e/captures/${mode}-agent-approval.png`, fullPage: true });
      await approve.click();
      await expect(banner).toHaveCount(0);
      expect(approved).toBe(1);
    });

    test('the Agent access card is display only', async ({ page }) => {
      await page.goto('/#/settings');
      const card = page.getByTestId('agent-access-card');
      await expect(card.getByLabel('MCP server address')).toHaveValue('http://mcp-dataworks.hitsai.local/mcp');
      await expect(card.getByLabel('MCP server address')).toHaveAttribute('readonly', '');
      await expect(card.getByTestId('gated-actions').locator('li')).toHaveCount(7);
      await expect(card.getByTestId('agent-activity')).toContainText('12 requests from 1 agent identity in 1 session');
      await expect(card.getByRole('button')).toHaveCount(0);
      await card.screenshot({ path: `e2e/captures/${mode}-agent-access.png` });
    });
  });
}

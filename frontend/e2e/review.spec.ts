import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api006 } from './fixtures006';

// Feature 006 (FTASKS 12.7, 12.10): the Review screen, keyboard-driven, in light and dark mode.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`review a queue with the keyboard (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api006(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/review');
    await expect(page.getByRole('heading', { level: 1, name: 'Review' })).toBeVisible();
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await expect(page.getByTestId('miforge-chip')).toContainText('miForge');
    await page.getByTestId('queue-card').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('item-text')).toContainText('cat mayor');
    const focused = page.locator(':focus-visible');
    expect(await focused.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
    await page.screenshot({ path: `e2e/captures/${mode}-review-item.png`, fullPage: true });
    await page.getByTestId('review-panel').focus();
    await page.keyboard.press('a');
    await expect.poll(() => seen.some((c) => c.method === 'POST' && c.path === '/api/v1/review-items/ri_1/decisions')).toBe(true);
    expect(seen.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'accept' });
    await expect(page.getByTestId('item-text')).toContainText('Senate passes budget bill');
    await page.keyboard.press('k');
    await expect(page.getByTestId('item-text')).toContainText('cat mayor');
    await page.getByRole('button', { name: 'Draw an audit' }).click();
    await expect(page.getByLabel('Audit size')).toHaveValue('100');
    await page.screenshot({ path: `e2e/captures/${mode}-review-audit.png`, fullPage: true });
  });
}

// Suite tracker 4.1 (D2): close a queue; closed queues leave the list unless asked for.
for (const mode of ['dark', 'light'] as const) {
  test(`close a queue and show closed queues (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api006(seen) });
    await page.addInitScript(theme, mode);
    page.on('dialog', (d) => void d.accept());
    await page.goto('/#/review');
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await page.getByTestId('queue-card').nth(1).click();
    await page.getByRole('button', { name: 'Close this queue' }).click();
    await expect(page.getByTestId('queue-card')).toHaveCount(1);
    expect(seen.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/api/v1/review-queues/rq_2/close']);
    await page.getByLabel('Show closed queues').check();
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await expect(page.getByTestId('queue-card').nth(1)).toContainText('closed');
    await page.screenshot({ path: `e2e/captures/${mode}-review-closed.png`, fullPage: true });
  });
}

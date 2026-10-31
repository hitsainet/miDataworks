import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api003 } from './fixtures003';

// The Operators screen (FR-003.22, task 11.10): catalogue, the min_len="abc" field error, a
// threshold drag, a preview and the allowlist, in both colour modes with visible focus.
for (const mode of ['dark', 'light'] as const) {
  test(`operators catalogue, form, threshold, preview and allowlist (${mode})`, async ({ page }) => {
    let statisticsCalls = 0;
    page.on('request', (r) => {
      if (r.url().endsWith('/statistics')) statisticsCalls += 1;
    });
    await stubApi(page, { extra: api003() });
    await page.addInitScript((theme) => {
      window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    }, mode);
    await page.goto('/#/operators');
    await expect(page.getByTestId('operator-card')).toHaveCount(3);
    await expect(page.getByTestId('operator-card').nth(2)).toContainText('Not allowed');
    await page.screenshot({ path: `e2e/captures/${mode}-operators.png`, });

    // allowlist: a reason is required, then the named button sends it
    await page.getByRole('button', { name: 'Allow tagger' }).click();
    await expect(page.getByText('Write a reason before changing the allowlist.')).toBeVisible();
    await page.getByLabel(/Reason/).fill('reviewed the package');
    await page.getByRole('button', { name: 'Allow tagger' }).click();

    // keyboard focus is visible on a card
    await page.getByLabel('State').focus();
    await page.keyboard.press('Tab');
    const focused = page.locator(':focus-visible');
    await expect(focused).toHaveAttribute('data-testid', 'operator-card');
    const ring = await focused.evaluate((el) => getComputedStyle(el).boxShadow);
    expect(ring).not.toBe('none');

    await page.getByTestId('operator-card').first().click();
    await expect(page.getByTestId('operator-detail')).toBeVisible();
    await page.getByLabel(/Minimum length/).fill('abc');
    await page.getByRole('button', { name: 'Check settings' }).click();
    await expect(page.getByTestId('field-error-min_len')).toContainText("'abc' is not of type 'integer'");

    await page.getByLabel('Version ID to sample from').fill('v1');
    await page.getByRole('button', { name: 'Preview on a sample' }).click();
    await expect(page.getByTestId('preview-dropped')).toContainText('too_short');
    await page.getByRole('button', { name: 'Show the statistic' }).click();
    await expect(page.getByTestId('drop-count')).toContainText('Drops 2 of 10 sample rows');
    const slider = page.getByLabel('min_len cutoff');
    await slider.focus();
    for (let i = 0; i < 15; i++) await page.keyboard.press('ArrowRight');
    await expect(page.getByTestId('drop-count')).not.toContainText('Drops 2 of 10');
    expect(statisticsCalls).toBe(1);
    await page.screenshot({ path: `e2e/captures/${mode}-operator-detail.png`, fullPage: true });
  });
}

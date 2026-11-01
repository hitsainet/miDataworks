import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api006 } from './fixtures006';

// Feature 006 (FTASKS 12.10): the Calibration screen in light and dark mode.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`calibration records, verdicts and checks (${mode})`, async ({ page }) => {
    await stubApi(page, { extra: api006() });
    await page.addInitScript(theme, mode);
    await page.goto('/#/calibration');
    await expect(page.getByRole('heading', { level: 1, name: 'Calibration' })).toBeVisible();
    const cards = page.getByTestId('record-card');
    await expect(cards).toHaveCount(2);
    await expect(cards.first().getByTestId('verdict-pill')).toContainText('Passes · level with held-out rater');
    await expect(cards.nth(1).getByTestId('verdict-pill')).toContainText('Fails · CI lower bound 0.660 < 0.70 (default)');
    await expect(cards.first().getByTestId('tile-paired')).toContainText('945 pairs, 745 groups');
    await expect(cards.first().getByTestId('check-line').first()).toHaveAttribute('data-result', 'fail');
    await expect(page.getByTestId('target-editor')).toContainText('default (C3)');
    await page.screenshot({ path: `e2e/captures/${mode}-calibration-records.png`, fullPage: true });
    await cards.first().getByRole('button', { name: 'Show as table' }).click();
    await expect(cards.first().getByTestId('reliability-table')).toContainText('under 30 rows');
    await page.getByRole('button', { name: 'Add a calibration set' }).click();
    await expect(page.getByTestId('add-set-dialog')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create calibration set' })).toBeDisabled();
    await page.screenshot({ path: `e2e/captures/${mode}-calibration-add-set.png`, fullPage: true });
  });
}

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';

// Version detail, Compare and "Why did this row leave?" (task 15.8), in both colour modes.
for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page, { extra: api002([]) });
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    test('a dataset card opens its head version; compare and row history answer', async ({ page }) => {
      await page.goto('/#/datasets');
      await page.getByTestId('dataset-card').click();
      await expect(page.getByRole('heading', { level: 1, name: 'humor-jev9b v2' })).toBeVisible();
      await expect(page.getByTestId('stat-tiles')).toContainText('14,086 rows');
      await expect(page.getByTestId('drop-step').first()).toContainText('13,195');
      await expect(page.getByTestId('drop-step').nth(1)).toContainText('891');
      await expect(page.getByText('1,090 rows').first()).toBeVisible();
      await page.screenshot({ path: `e2e/captures/${mode}-version-detail.png`, fullPage: true });

      await page.getByRole('button', { name: 'Compare with v1' }).click();
      await expect(page.getByTestId('compare-view')).toContainText('Characters in text, v2 on 10,914 rows, v1 on 25,000 rows');
      await page.getByTestId('compare-view').screenshot({ path: `e2e/captures/${mode}-compare.png` });

      await page.getByLabel('Row key or text').fill('pig that took a tour');
      await page.getByRole('button', { name: 'Why did this row leave?' }).click();
      const drawer = page.getByRole('dialog', { name: 'Why did this row leave?' });
      await expect(drawer.getByTestId('row-status')).toHaveText('Dropped');
      await expect(drawer).toContainText('step 1 (threshold_labeler 1)');
      await page.screenshot({ path: `e2e/captures/${mode}-row-history.png` });
      await drawer.getByRole('heading', { name: 'Why did this row leave?' }).click();
      await page.keyboard.press('Tab');
      const ring = await page.locator(':focus-visible').evaluate((el) => getComputedStyle(el).boxShadow);
      expect(ring).not.toBe('none');
    });
  });
}

test('the error state names the problem when a version cannot load', async ({ page }) => {
  await stubApi(page, { extra: (path) => (path === '/api/v1/versions' ? { items: [], total: 0 } : path === '/api/v1/datasets/meta' ? {} : undefined) });
  await page.addInitScript(() => window.localStorage.setItem('midataworks-selected-version', 'v-gone'));
  await page.goto('/#/version');
  await expect(page.getByText('not stubbed')).toBeVisible();
});

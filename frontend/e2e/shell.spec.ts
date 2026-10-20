import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';

// Every registered screen, in both colour modes (task 12.12). Captures are saved for review.
const SCREENS = [
  ['datasets', 'Datasets'], ['new-dataset', 'New dataset'], ['version', 'Version detail'],
  ['recipes', 'Recipes'], ['label-runs', 'Label runs'], ['calibration', 'Calibration'],
  ['review', 'Review'], ['generation', 'Generation'], ['detector-sets', 'Detector sets'],
  ['operators', 'Operators'], ['publish', 'Publish and export'], ['settings', 'Settings'],
] as const;

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page);
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    for (const [id, title] of SCREENS) {
      test(`${title} opens`, async ({ page }) => {
        await page.goto(`/#/${id}`);
        await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
        await expect(page.locator('html')).toHaveClass(mode === 'dark' ? /dark/ : /^(?!.*dark).*$/);
        await expect(page.getByTestId('chip-millm')).toContainText('connected');
        await page.screenshot({ path: `e2e/captures/${mode}-${id}.png`, fullPage: true });
      });
    }

    test('sidebar measures 224 px and the top bar 56 px', async ({ page }) => {
      await page.goto('/#/datasets');
      const sidebar = await page.getByTestId('sidebar').boundingBox();
      const topbar = await page.getByTestId('topbar').boundingBox();
      expect(sidebar?.width).toBe(224);
      expect(topbar?.height).toBe(56);
    });

    test('focus rings are visible on keyboard focus', async ({ page }) => {
      await page.goto('/#/datasets');
      await page.keyboard.press('Tab');
      const focused = page.locator(':focus-visible');
      await expect(focused).toHaveCount(1);
      const shadow = await focused.evaluate((el) => getComputedStyle(el).boxShadow);
      expect(shadow).not.toBe('none');
      await page.screenshot({ path: `e2e/captures/${mode}-focus.png` });
    });
  });
}

test('the loading state renders while settings load', async ({ page }) => {
  await stubApi(page, { slowSettings: true });
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await expect(page.getByTestId('settings-loading')).toBeVisible();
  await expect(page.getByTestId('settings-loading')).toBeHidden({ timeout: 5000 });
});

test('the error state renders when the API is unreachable', async ({ page }) => {
  await stubApi(page, { failHealth: true });
  await page.goto('/#/datasets');
  await expect(page.getByText('The miDataworks API is unreachable; figures are stale.')).toBeVisible();
  await page.screenshot({ path: 'e2e/captures/error-unreachable.png' });
});

test('below 768 px the sidebar becomes a tab strip', async ({ page }) => {
  await stubApi(page);
  await page.setViewportSize({ width: 600, height: 900 });
  await page.goto('/#/datasets');
  await expect(page.getByTestId('sidebar')).toBeHidden();
  await expect(page.getByTestId('mobile-tabs')).toBeVisible();
});

test('the built app serves the favicon the page links, not the app shell', async ({ page, request }) => {
  await stubApi(page);
  await page.goto('/#/datasets');
  const href = await page.locator('link[rel="icon"]').getAttribute('href');
  expect(href).toBe('/favicon.svg');
  const response = await request.get(href!);
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('image/svg+xml');
  const body = await response.text();
  expect(body).toContain('<svg');
  expect(body).not.toContain('<!doctype html>');
});

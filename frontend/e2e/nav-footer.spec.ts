import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';

// The sidebar foot matches the suite (operator, 2026-10-20; miStudio and miLLM): Settings at the
// bottom, the Manual book icon to its right on the same row, the version below in small muted
// monospace. Captures go to e2e/captures/ (git-ignored), or NAVFOOT_CAPTURE_DIR.
const DIR = process.env.NAVFOOT_CAPTURE_DIR ?? 'e2e/captures';
const VERSION = (
  JSON.parse(readFileSync(resolve(process.cwd(), 'package.json'), 'utf8')) as { version: string }
).version;

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page);
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.addInitScript((theme) => {
        window.localStorage.setItem(
          'midataworks-ui-preferences',
          JSON.stringify({
            state: { theme, sidebar: { collapsed: false, mobileOpen: false } },
            version: 0,
          }),
        );
      }, mode);
    });

    test('the foot is Settings + Manual on one row, the version below, at the bottom of the sidebar', async ({
      page,
    }) => {
      await page.goto('/#/datasets');
      await expect(page.getByTestId('chip-millm')).toContainText('connected');
      const sidebar = (await page.getByTestId('sidebar').boundingBox())!;
      const foot = (await page.getByTestId('sidebar-foot').boundingBox())!;
      const settings = (await page.getByTestId('nav-settings').boundingBox())!;
      const manual = (await page.getByTestId('sidebar-manual').boundingBox())!;
      const version = (await page.getByTestId('sidebar-version').boundingBox())!;

      // Pinned to the bottom of the full-height sidebar.
      expect(Math.round(foot.y + foot.height)).toBe(Math.round(sidebar.y + sidebar.height));
      // Manual sits to the right of Settings, vertically centred on the same row.
      expect(manual.x).toBeGreaterThanOrEqual(settings.x + settings.width - 1);
      expect(manual.x + manual.width).toBeLessThanOrEqual(sidebar.x + sidebar.width);
      expect(
        Math.abs(manual.y + manual.height / 2 - (settings.y + settings.height / 2)),
      ).toBeLessThan(2);
      // The version is the row below, left-aligned with the Settings label inset.
      expect(version.y).toBeGreaterThanOrEqual(settings.y + settings.height);
      await expect(page.getByTestId('sidebar-version')).toHaveText(`v${VERSION}`);
      const style = await page.getByTestId('sidebar-version').evaluate((el) => {
        const s = getComputedStyle(el);
        return { size: s.fontSize, family: s.fontFamily };
      });
      expect(style.size).toBe('10px');
      expect(style.family).toMatch(/mono/i);

      const link = page.getByRole('link', { name: 'User manual (opens in a new tab)' });
      await expect(link).toHaveAttribute('href', 'https://hitsainet.github.io/miDataworks/');
      await expect(link).toHaveAttribute('target', '_blank');
      // Help is in the main list, not the foot.
      await expect(page.getByTestId('sidebar-foot').getByTestId('nav-help')).toHaveCount(0);
      await expect(
        page.getByRole('navigation', { name: 'Screens' }).getByTestId('nav-help'),
      ).toBeVisible();

      await page.getByTestId('sidebar').screenshot({ path: `${DIR}/after-${mode}-sidebar.png` });
      await page.screenshot({ path: `${DIR}/after-${mode}-full.png` });
    });

    test('keyboard focus runs Settings, then Manual', async ({ page }) => {
      await page.goto('/#/datasets');
      await page.getByTestId('nav-settings').focus();
      await page.keyboard.press('Tab');
      await expect(page.getByTestId('sidebar-manual')).toBeFocused();
    });

    test('collapsed, the foot keeps Settings and drops the Manual link and version', async ({
      page,
    }) => {
      await page.goto('/#/datasets');
      await page.getByRole('button', { name: 'Collapse the sidebar' }).click();
      await expect(page.getByTestId('sidebar')).toHaveCSS('width', '64px');
      await expect(page.getByTestId('nav-settings')).toBeVisible();
      await expect(page.getByTestId('sidebar-manual')).toHaveCount(0);
      await expect(page.getByTestId('sidebar-version')).toHaveCount(0);
      await page
        .getByTestId('sidebar')
        .screenshot({ path: `${DIR}/after-${mode}-sidebar-collapsed.png` });
    });
  });
}

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';

// Recipes CRUD surface (FR-002.41; task 19.4's captures), both colour modes.
for (const mode of ['dark', 'light'] as const) {
  test(`recipes list, editor and per-step errors (${mode})`, async ({ page }) => {
    await stubApi(page, { extra: api002([]) });
    await page.addInitScript((theme) => {
      window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    }, mode);
    await page.goto('/#/recipes');
    await expect(page.getByTestId('recipe-card')).toHaveCount(2);
    await expect(page.getByTestId('recipe-card').first()).toContainText('3 steps');
    await page.screenshot({ path: `e2e/captures/${mode}-recipes.png`, fullPage: true });
    await page.getByRole('button', { name: 'New recipe' }).click();
    await page.getByLabel('Operator').fill('text_length_filter');
    await page.getByLabel('Version').fill('1.2');
    await page.getByRole('button', { name: 'Check recipe' }).click();
    await expect(page.getByTestId('step-errors')).toContainText('Clone recipe with current operators');
    await page.screenshot({ path: `e2e/captures/${mode}-recipe-editor.png`, fullPage: true });
  });
}

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';

// The guided New dataset flow (task 17.7): all seven steps reachable from the rail, the Goal step
// creates the dataset, 001's Import step renders, 004's Profile, Curate and Assemble render, unbuilt steps name their owner, the draft is saved; both colour modes.
for (const mode of ['dark', 'light'] as const) {
  test(`walk the seven steps (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api002(seen) });
    await page.addInitScript((theme) => {
      window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    }, mode);
    await page.goto('/#/new-dataset');
    const rail = page.getByTestId('step-rail');
    await expect(rail.locator('li')).toHaveCount(7);
    await expect(page.getByRole('heading', { name: 'What will train on this dataset?' })).toBeVisible();
    await page.getByLabel(/Dataset name/).fill('humor');
    await page.getByLabel('Goal').selectOption('detector');
    await page.getByRole('button', { name: 'Create the dataset' }).click();
    // Feature 004 builds Profile, Curate and Assemble; without a version they say so.
    await expect(page.getByRole('heading', { name: 'Profile the data' })).toBeVisible();
    await expect(page.getByText('Choose or build a version first; the profile reads a version.')).toBeVisible();
    await page.screenshot({ path: `e2e/captures/${mode}-guided-flow.png`, fullPage: true });
    // Feature 001 builds the Import step: the same form and upload as the Datasets screen.
    await rail.getByText('import', { exact: true }).click();
    await expect(page.getByTestId('guided-import').getByText('Import from Hugging Face')).toBeVisible();
    await expect(page.getByTestId('guided-import').getByText('Upload a file')).toBeVisible();
    await page.screenshot({ path: `e2e/captures/${mode}-guided-import.png`, fullPage: true });
    // Feature 005 builds the Label step.
    await rail.getByText('label', { exact: true }).click();
    await expect(page.getByTestId('label-step')).toBeVisible();
    await rail.getByText('curate', { exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Curate' })).toBeVisible();
    await expect(page.getByText('dedup_minhash')).toBeVisible();
    await rail.getByText('export', { exact: true }).click();
    // 008's export step (37a8bf4 gave it a panel; this line still expected the placeholder).
    await expect(page.getByTestId('guided-export-step')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Generation' })).toHaveCount(0);
    await rail.getByText('assemble', { exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Assemble' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Generation' })).toBeVisible();
    await expect.poll(() => seen.some((c) => c.method === 'PUT' && c.path === '/api/v1/recipe-drafts/draft-1')).toBe(true);
    expect(seen.find((c) => c.method === 'POST' && c.path === '/api/v1/datasets')?.body).toEqual({ name: 'humor', target_type: 'detector' });
  });
}

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';
import { api007 } from './fixtures007';

// Feature 007 (FTASKS 12.10): a standard run, the refusal paths, the steered form's refusal and the
// diversity slot — light and dark, keyboard operable with visible focus rings.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`generation: list, detail and a standard run (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api007(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/generation');
    await expect(page.getByRole('heading', { level: 1, name: 'Generation' })).toBeVisible();
    const card = page.getByTestId('generation-run-card');
    await expect(card).toContainText('1,800 generated · 200 discarded');
    await card.focus();
    await page.keyboard.press('Enter');
    const detail = page.getByTestId('generation-run-detail');
    await expect(detail.getByTestId('identities')).toContainText('JEV-9B-decision');
    await expect(detail.getByTestId('discard-list')).toContainText('not reported');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-detail.png`, fullPage: true });
    await page.getByRole('button', { name: 'New standard run' }).click();
    const form = page.getByTestId('new-standard-run');
    await form.getByLabel('Input version').selectOption('v-sft-0001');
    await form.getByRole('button', { name: 'Check the plan' }).click();
    await expect(form.getByTestId('held-out-status')).toContainText('Held-out split: test');
    await expect(form.getByTestId('plan-summary')).toContainText('miLLM serves one model at a time');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-new-run.png`, fullPage: true });
    await form.getByRole('button', { name: 'Check the plan' }).focus();
    await page.keyboard.press('Tab'); // keyboard focus, so the ring must show
    const focused = page.locator(':focus-visible');
    await expect(focused).toHaveText(/Generate/);
    expect(await focused.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
    await page.keyboard.press('Enter');
    await expect.poll(() => seen.some((c) => c.method === 'POST' && c.path === '/api/v1/generation-runs')).toBe(true);
    expect(seen.find((c) => c.method === 'POST' && c.path === '/api/v1/generation-runs')?.body).toMatchObject({ mode: 'standard', input_version_id: 'v-sft-0001', generator_setting: { kind: 'none' } });
  });

  test(`generation: refusal paths (${mode})`, async ({ page }) => {
    await stubApi(page, { extra: api007() });
    await page.addInitScript(theme, mode);
    await page.goto('/#/generation');
    await page.getByRole('button', { name: 'New standard run' }).click();
    const form = page.getByTestId('new-standard-run');
    await form.getByLabel('Input version').selectOption('v-nosplit');
    await form.getByRole('button', { name: 'Check the plan' }).click();
    await expect(form.getByTestId('held-out-status')).toContainText('No held-out split yet');
    await expect(form.getByRole('button', { name: /^Generate .* responses$/ })).toBeDisabled();
    await page.getByRole('button', { name: 'New steered-pair run' }).click();
    const steered = page.getByTestId('new-steered-run');
    await expect(steered.getByTestId('axis-diff')).toContainText('A steered pair must differ on exactly one feature');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-refusals.png`, fullPage: true });
  });

  test(`version detail: the diversity slot (${mode})`, async ({ page }) => {
    const a2 = api002([]);
    const a7 = api007();
    await stubApi(page, { extra: (p, m, u, b) => a7(p, m, u, b) ?? a2(p, m, u, b) });
    await page.addInitScript(theme, mode);
    await page.addInitScript(() => window.localStorage.setItem('midataworks-selected-version', 'v-2'));
    await page.goto('/#/version');
    const panel = page.getByTestId('diversity-panel');
    await expect(panel).toContainText('distinct-2');
    await expect(panel.getByRole('alert')).toContainText('Cap the largest cluster or generate from more seed rows.');
    await expect(panel).toContainText('not measured');
    await page.screenshot({ path: `e2e/captures/${mode}-version-diversity.png`, fullPage: true });
  });
}

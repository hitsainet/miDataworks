import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';
import { api005 } from './fixtures005';

// Feature 005 (FTASKS 14.9): the Label step, Label runs list and detail, and the Settings role
// cards' Test endpoint — light and dark, keyboard operable with visible focus rings.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`label step: sample, plan and start (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    const a2 = api002([]);
    const a5 = api005(seen);
    await stubApi(page, { extra: (p, m, u, b) => a5(p, m, u, b) ?? a2(p, m, u, b) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await expect(step).toBeVisible();
    await expect(step.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    await step.getByLabel('Version to label').selectOption({ index: 1 });
    await step.getByLabel('Decision template').selectOption('dt_1');
    await step.getByLabel('Question').fill('Is this text intended to be humorous?');
    await step.getByLabel('Positive at or above').fill('0.5');
    await step.getByLabel('Negative at or below').fill('0.2');
    await expect(step.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled();
    await step.getByRole('button', { name: 'Try it on a sample' }).click();
    await expect(step.getByTestId('sample-panel')).toContainText('P 0.610');
    await expect(step.getByTestId('calibration-callout')).toContainText('Not calibrated yet');
    await page.screenshot({ path: `e2e/captures/${mode}-label-step.png`, fullPage: true });
    // keyboard: Tab from the last field reaches the start button, with a visible focus ring
    await step.getByLabel('Negative at or below').focus();
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press('Tab');
      if ((await page.locator(':focus').textContent()) === 'Label 25,000 rows') break;
    }
    const focused = page.locator(':focus-visible');
    await expect(focused).toHaveText('Label 25,000 rows');
    expect(await focused.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
    await page.keyboard.press('Enter');
    await expect.poll(() => seen.some((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')).toBe(true);
    expect(seen.find((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')?.body).toMatchObject({ threshold_positive: 0.5, threshold_negative: 0.2, role: 'classifier' });
  });

  test(`label step: a probe in miLLM plans with its bar and reproduction gate (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    const a2 = api002([]);
    const a5 = api005(seen);
    await stubApi(page, { extra: (p, m, u, b) => a5(p, m, u, b) ?? a2(p, m, u, b) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await step.getByLabel('Version to label').selectOption({ index: 1 });
    await step.getByLabel('Label with').selectOption('probe');
    await expect(step.getByLabel('Question')).toHaveCount(0);
    await expect(step.getByLabel('Positive at or above')).toHaveCount(0);
    await expect(step.getByRole('button', { name: 'Estimate keep share' })).toHaveCount(0);
    await step.getByLabel('Probe', { exact: true }).selectOption('pr_5ac12236c0dd');
    await expect(step.getByLabel('Window', { exact: true })).toHaveValue('all');
    await expect(step.getByTestId('probe-plan-line')).toContainText('bar 11.914 (probe score, not a probability)');
    await expect(step.getByTestId('probe-plan-line')).toContainText('generalizes to unseen tasks; compared with a judge');
    await expect(step.getByTestId('reproduction-line')).toContainText("requires an AUROC inside miStudio's [0.874, 0.907]");
    await expect(step.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled();
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-probe.png`, fullPage: true });
    await step.getByRole('button', { name: 'Label 25,000 rows' }).click();
    await expect.poll(() => seen.some((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')).toBe(true);
    expect(seen.find((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')?.body).toEqual({
      input_version_id: expect.any(String), role: 'probe', probe: { probe_id: 'pr_5ac12236c0dd', window: 'all' }, field_map: { text: 'text' },
    });
  });

  test(`label runs list and detail (${mode})`, async ({ page }) => {
    await stubApi(page, { extra: api005() });
    await page.addInitScript(theme, mode);
    await page.goto('/#/label-runs');
    await expect(page.getByTestId('label-run-card')).toHaveCount(2);
    await expect(page.getByTestId('label-run-card').nth(1)).toContainText('Unpinned');
    for (const card of await page.getByTestId('label-run-card').all()) await expect(card).not.toContainText('— rows/s');
    await page.getByTestId('label-run-card').first().focus();
    await page.keyboard.press('Enter');
    const detail = page.getByTestId('label-run-detail');
    await expect(detail).toBeVisible();
    await expect(detail.getByTestId('provenance')).toContainText('JEV-9B-decision');
    await expect(detail.getByTestId('probability-histogram')).toBeVisible();
    await expect(detail.getByRole('button', { name: 'Cancel run' })).toBeVisible();
    await page.screenshot({ path: `e2e/captures/${mode}-label-runs.png`, fullPage: true });
  });

  test(`settings role card tests its endpoint (${mode})`, async ({ page }) => {
    await stubApi(page, {
      extra: (path, method, url, body) => {
        if (path === '/api/v1/endpoint-roles') {
          return ['classifier', 'judge', 'generation', 'embeddings'].map((r) => ({
            role: r, configured: r === 'classifier', protocol: r === 'classifier' ? 'openai_scoring' : null,
            base_url: r === 'classifier' ? 'http://millm/v1' : null, model_id: r === 'classifier' ? 'JEV-9B-decision' : null,
            api_key: null, has_api_key: false, inherit_from_judge: r === 'generation' || r === 'embeddings', use_mode: 'own', effective_role: r === 'classifier' ? 'classifier' : null,
          }));
        }
        return api005()(path, method, url, body);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/settings');
    const card = page.getByTestId('role-classifier');
    await expect(card.getByTestId('template-picker')).toContainText('jev/noul-bare-v1@1');
    await card.getByRole('button', { name: 'Test endpoint' }).click();
    await expect(card.getByTestId('capability-classifier')).toContainText('miLLM, JEV-9B-decision loaded, lease free');
    await page.screenshot({ path: `e2e/captures/${mode}-settings-roles.png`, fullPage: true });
  });
}

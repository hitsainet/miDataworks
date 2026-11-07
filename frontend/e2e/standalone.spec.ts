import { expect, test } from '@playwright/test';

import { HEALTH_STANDALONE, stubApi, type Extra } from './fixtures';
import { api007 } from './fixtures007';
import { api009 } from './fixtures009';

// miDataworks stands alone (operator principle, 2026-10-07): with MILLM_BASE_URL and
// MISTUDIO_BASE_URL both unset, every screen renders, the chips say "not configured", and the
// integration-only screens say why they cannot act instead of erroring or spinning — in light and
// dark mode. Audit: 0xcc/reviews/standalone_audit_2026-10-07.md.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

const SCREENS = ['datasets', 'new-dataset', 'recipes', 'label-runs', 'calibration', 'review', 'generation', 'detector-sets', 'operators', 'publish', 'settings'];

/** The generation endpoint answers its test as a generic OpenAI-compatible server. */
const genericGeneration = (inner: Extra): Extra => (path, method, url, body) => {
  if (path === '/api/v1/endpoint-roles/generation/test' && method === 'POST') {
    return { role: 'generation', reachable: true, model_listed: true, protocol_ok: true, server_kind: 'openai_compatible', resident_model: null, lease_supported: false, lease_state: null, queue: null, error_code: null, message: 'generic-chat-7b answers (openai_compatible).' };
  }
  return inner(path, method, url, body);
};

for (const mode of ['light', 'dark'] as const) {
  test(`standalone: every screen renders with no sibling configured (${mode})`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await stubApi(page, { health: HEALTH_STANDALONE });
    await page.addInitScript(theme, mode);
    for (const id of SCREENS) {
      await page.goto(`/#/${id}`);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.getByText('Something went wrong')).toHaveCount(0);
    }
    await expect(page.locator('header')).toContainText('miLLM · not configured');
    await expect(page.locator('header')).toContainText('miStudio · not configured');
    await page.goto('/#/settings');
    await expect(page.getByText('not configured (deployment setting)')).toHaveCount(2);
    await page.screenshot({ path: `e2e/captures/${mode}-standalone-settings.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test(`standalone: a detector set says sending needs miStudio and sends nothing (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { health: HEALTH_STANDALONE, extra: api009(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/detector-sets');
    await page.getByRole('button', { name: 'New detector set' }).click();
    await page.getByLabel('Detector set name').fill('humor-set');
    await page.getByLabel('Training rows Version ID').fill('v1');
    await page.getByLabel('Training rows label mapping').fill('humorous=positive, not_humorous=negative');
    await page.getByRole('button', { name: 'Create detector set' }).click();
    await expect(page.getByTestId('set-detail')).toBeVisible();
    await page.getByRole('button', { name: 'Run checks' }).click();
    await page.getByLabel('Hugging Face namespace').fill('someone');
    await expect(page.getByTestId('mistudio-unconfigured')).toContainText('publish its versions to the Hugging Face Hub');
    await expect(page.getByRole('button', { name: 'Send to miStudio' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Refresh results' })).toBeDisabled();
    await expect(page.getByTestId('send-blocked')).toContainText('Sending needs miStudio');
    await expect(page.getByTestId('no-results')).toContainText('only from a configured miStudio');
    await page.screenshot({ path: `e2e/captures/${mode}-standalone-detector-set.png`, fullPage: true });
    expect(seen.filter((c) => c.path.endsWith('/send') || c.path.endsWith('/results/refresh'))).toEqual([]);
  });

  test(`standalone: the steered-pair form says steering needs miLLM on a generic endpoint (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { health: HEALTH_STANDALONE, extra: genericGeneration(api007(seen)) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/generation');
    await page.getByRole('button', { name: 'New steered-pair run' }).click();
    const form = page.getByTestId('new-steered-run');
    await expect(form.getByTestId('steering-unavailable')).toContainText('Steered pairs need miLLM');
    await expect(form.getByRole('button', { name: 'Check the plan' })).toBeDisabled();
    await page.screenshot({ path: `e2e/captures/${mode}-standalone-steered.png`, fullPage: true });
    expect(seen.filter((c) => c.path === '/api/v1/generation-runs/plan')).toEqual([]);
  });
}

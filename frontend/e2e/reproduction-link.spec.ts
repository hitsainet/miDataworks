import { readFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';
import { api005 } from './fixtures005';

// 009 FR-009.77 option (b): an imported probe's plan is refused with its facts, the operator links a
// recorded miStudio evaluation, the plan then names the link and how its rows were checked. Bodies are
// the backend's own (src/test/captured009/). Light and dark; and the probe option without miLLM.
const captured = (name: string): unknown =>
  JSON.parse(readFileSync(new URL(`../src/test/captured009/reproduction_${name}.json`, import.meta.url), 'utf8'));
const LINK = captured('link');
const PLAN_LINKED = captured('plan_linked');
const REFUSAL = captured('refusal');
const LINK_SERVED = captured('link_served') as { scoring_form: unknown };
const PLAN_SERVED = (() => {
  const p = structuredClone(PLAN_LINKED) as { reproduction: { scoring_form: unknown } };
  p.reproduction.scoring_form = LINK_SERVED.scoring_form;
  return p;
})();

const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`label step: a served-render probe's link reads equal by render rule (${mode})`, async ({ page }) => {
    let linked = false;
    const a2 = api002([]);
    const a5 = api005();
    await stubApi(page, {
      extra: (p, m, u, b) => {
        if (p === '/api/v1/label-runs/plan') {
          const request = JSON.parse(u.searchParams.get('request') ?? '{}') as { role?: string };
          if (request.role === 'probe') return linked ? PLAN_SERVED : { status: 422, body: REFUSAL };
        }
        if (p === '/api/v1/reproduction-links' && m === 'POST') {
          linked = true;
          return { status: 201, body: LINK_SERVED };
        }
        return a5(p, m, u, b) ?? a2(p, m, u, b);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await step.getByLabel('Version to label').selectOption({ index: 1 });
    await step.getByLabel('Label with').selectOption('probe');
    await step.getByLabel('Probe', { exact: true }).selectOption('pr_5ac12236c0dd');
    const block = step.getByTestId('reproduction-refusal');
    await block.getByRole('button', { name: 'Link a miStudio evaluation' }).click();
    await block.getByLabel('miStudio evaluation').selectOption('pmd_fb14b0b206a5');
    await block.getByLabel('Version holding those rows').selectOption({ index: 1 });
    await block.getByRole('button', { name: 'Check and link' }).click();
    const form = step.getByTestId('scoring-form');
    await expect(form).toHaveAttribute('data-agreement', 'equal_by_render_rule');
    await expect(form).toContainText('Equal by render rule, token ids not compared');
    await expect(form).toContainText('WITH the generation prompt');
    await expect(form).not.toContainText('no generation prompt');
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-linked-served.png`, fullPage: true });
  });

  test(`label step: link a miStudio evaluation for an imported probe (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    let linked = false;
    const a2 = api002([]);
    const a5 = api005(seen);
    await stubApi(page, {
      extra: (p, m, u, b) => {
        if (p === '/api/v1/label-runs/plan') {
          const request = JSON.parse(u.searchParams.get('request') ?? '{}') as { role?: string };
          if (request.role === 'probe') return linked ? PLAN_LINKED : { status: 422, body: REFUSAL };
        }
        if (p === '/api/v1/reproduction-links' && m === 'POST') {
          seen.push({ method: m, path: p, body: b });
          linked = true;
          return { status: 201, body: LINK };
        }
        return a5(p, m, u, b) ?? a2(p, m, u, b);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await step.getByLabel('Version to label').selectOption({ index: 1 });
    await step.getByLabel('Label with').selectOption('probe');
    await step.getByLabel('Probe', { exact: true }).selectOption('pr_5ac12236c0dd');
    const block = step.getByTestId('reproduction-refusal');
    await expect(block.getByTestId('probe-preflight')).toContainText('against bar 2.500');
    await expect(block.getByTestId('reproduction-ways')).toContainText('link a version split holding the rows miStudio evaluated');
    await block.getByRole('button', { name: 'Link a miStudio evaluation' }).click();
    await block.getByLabel('miStudio evaluation').selectOption('pmd_fb14b0b206a5');
    await block.getByLabel('Version holding those rows').selectOption({ index: 1 });
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-link.png`, fullPage: true });
    await block.getByRole('button', { name: 'Check and link' }).click();
    await expect(step.getByTestId('reproduction-target')).toContainText('rows checked by content hash');
    // pm_c99519a98e08 records no render form: the backend says the forms differ, and why.
    await expect(step.getByTestId('scoring-form')).toContainText('These DIFFER');
    await expect(step.getByTestId('scoring-form')).toContainText('not recorded - rendered without the generation prompt');
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-linked.png`, fullPage: true });
    expect(seen.find((c) => c.method === 'POST' && c.path === '/api/v1/reproduction-links')?.body).toMatchObject({
      mistudio_probe_id: 'pm_c99519a98e08', probe_dataset_id: 'pmd_fb14b0b206a5', split: 'test',
    });
  });

  test(`label step: the probe option says miLLM is not configured (${mode})`, async ({ page }) => {
    const a2 = api002([]);
    const a5 = api005();
    await stubApi(page, {
      extra: (p, m, u, b) =>
        p === '/api/v1/labeling/probes'
          ? { status: 409, body: { error: { code: 'PROBE_ENDPOINT_UNCONFIGURED', message: 'Probe scoring runs on miLLM, and MILLM_BASE_URL is not set.', details: { setting: 'MILLM_BASE_URL' } } } }
          : a5(p, m, u, b) ?? a2(p, m, u, b),
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await expect(step.getByLabel('Label with').locator('option[value="probe"]')).toHaveText('A probe in miLLM (miLLM not configured)');
    await step.getByLabel('Label with').selectOption('probe');
    await expect(step.getByTestId('probes-unavailable')).toContainText('MILLM_BASE_URL is not set');
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-no-millm.png`, fullPage: true });
  });
}

import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { CHAIN_DONE, CHAIN_STOPPED } from '../src/test/fixtures009';
import { api009 } from './fixtures009';

// Feature 009 (FTASKS 9.9): create, check, send, refresh — in light and dark mode — and the screen
// below 768 px, where the sidebar becomes a tab strip.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`detector set: create, check, send, refresh (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api009(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/detector-sets');
    await expect(page.getByRole('heading', { level: 1, name: 'Detector sets' })).toBeVisible();
    await expect(page.getByTestId('no-sets')).toBeVisible();
    await page.getByRole('button', { name: 'New detector set' }).click();
    await page.getByLabel('Detector set name').fill('humor-set');
    await page.getByLabel('Training rows Version ID').fill('v1');
    await page.getByLabel('Training rows label mapping').fill('humorous=positive, not_humorous=negative');
    await page.screenshot({ path: `e2e/captures/${mode}-detector-sets-new.png`, fullPage: true });
    await page.getByRole('button', { name: 'Create detector set' }).click();
    await expect(page.getByTestId('set-detail')).toBeVisible();
    const createBody = seen.find((c) => c.method === 'POST' && c.path === '/api/v1/detector-sets')?.body as { name: string; roles: Array<{ role: string; label_mapping: Record<string, string> }> };
    expect(createBody.name).toBe('humor-set');
    expect(createBody.roles.map((r) => r.role)).toEqual(['train', 'id_test', 'ood_eval', 'calibration_negatives']);
    expect(createBody.roles[0].label_mapping).toEqual({ humorous: 'positive', not_humorous: 'negative' });
    await expect(page.getByTestId('role-table')).toContainText('Calibration negatives');
    await page.getByRole('button', { name: 'Run checks' }).click();
    await expect(page.getByTestId('check-D-5')).toContainText('Note');
    await expect(page.getByTestId('length-profiles')).toContainText('n = 2,000 rows');
    await page.getByLabel('Hugging Face namespace').fill('mistudio');
    await page.screenshot({ path: `e2e/captures/${mode}-detector-sets-checks.png`, fullPage: true });
    await page.getByRole('button', { name: 'Send to miStudio' }).click();
    await expect(page.getByTestId('send-progress')).toBeVisible();
    await expect(page.getByTestId('run-request')).toContainText('pmd_ood', { timeout: 15_000 });
    await page.getByRole('button', { name: 'Refresh results' }).click();
    await expect(page.getByTestId('rung-pill')).toContainText('detects on unseen tasks');
    await expect(page.getByTestId('probe-pm_935fc9088482')).toContainText('0.7375 [0.7258, 0.7502]');
    await page.screenshot({ path: `e2e/captures/${mode}-detector-sets-results.png`, fullPage: true });
    const sends = seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/detector-sets/dts_1/send');
    expect(sends).toHaveLength(1);
    expect(sends[0].body).toEqual({ namespace: 'mistudio', visibility: 'private' });
  });
}

test('below 768 px the detector sets screen keeps the tab strip and no horizontal scroll', async ({ page }) => {
  await stubApi(page, { extra: api009() });
  await page.setViewportSize({ width: 767, height: 900 });
  await page.goto('/#/detector-sets');
  await expect(page.getByRole('heading', { level: 1, name: 'Detector sets' })).toBeVisible();
  await expect(page.getByTestId('sidebar')).toBeHidden();
  await expect(page.getByTestId('mobile-tabs')).toBeVisible();
  const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(scroll).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `e2e/captures/mobile-detector-sets.png`, fullPage: true });
});

// Minimal pairs as a chain (009 FTASKS 15.x; operator decision 2026-10-07): a chain stopped at the
// judge names the stage and the reason; Resume continues it there, and the counts come back.
for (const mode of ['dark', 'light'] as const) {
  test(`minimal pairs: a stopped chain names its stage and resumes (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    let resumed = false;
    await stubApi(page, {
      extra: (path, method, _url, body) => {
        seen.push({ method, path, body });
        if (path === '/api/v1/minimal-pair-chains' && method === 'GET') return { items: [resumed ? CHAIN_DONE : CHAIN_STOPPED], total: 1 };
        if (path === '/api/v1/minimal-pair-chains/mpc_1/resume' && method === 'POST') {
          resumed = true;
          return CHAIN_DONE;
        }
        return undefined;
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/detector-sets');
    const chain = page.getByTestId('chain-mpc_1');
    await expect(chain.getByTestId('stage-judge')).toContainText('Stopped here');
    await expect(chain.getByTestId('chain-error')).toContainText('MODEL_NOT_LOADED');
    await expect(chain.getByTestId('stage-scope')).toContainText('version v_scope');
    await page.screenshot({ path: `e2e/captures/${mode}-minimal-pairs-stopped.png`, fullPage: true });
    await chain.getByRole('button', { name: 'Resume' }).click();
    await expect(chain.getByTestId('chain-counts')).toContainText('2 of 4 judged pairs verified');
    await page.screenshot({ path: `e2e/captures/${mode}-minimal-pairs-done.png`, fullPage: true });
    expect(seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/minimal-pair-chains/mpc_1/resume')).toHaveLength(1);
  });
}

// Set-check fixes (2026-10-08): a role declares the columns its label was computed from, and the
// calibration negatives can be labelled negative by people — in light and dark mode.
for (const mode of ['dark', 'light'] as const) {
  test(`detector set: label source columns and a human-labelled basis (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api009(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/detector-sets');
    await page.getByRole('button', { name: 'New detector set' }).click();
    await page.getByLabel('Detector set name').fill('humicroedit-funny');
    await page.getByLabel('Training rows label mapping').fill('1=positive, 0=negative, None=excluded');
    await page.getByLabel('Training rows label source columns').fill('meanGrade, grades');
    await page.getByLabel('In-distribution test label source columns').fill('meanGrade, grades');
    await page.getByLabel('Calibration negatives Label column').fill('human_label');
    await page.getByLabel('Calibration negatives label mapping').fill('0=negative, 1=excluded, None=excluded');
    await page.getByLabel('Calibration negatives basis').selectOption('human_labelled');
    await expect(page.getByTestId('human-basis')).toContainText('Negatives are the rows whose human_label is 0');
    await page.getByLabel('Labelled by').fill('five Humicroedit graders per edited headline');
    await page.getByLabel('Human labelling rule').fill('meanGrade <= 0.4, or an unedited original');
    await page.screenshot({ path: `e2e/captures/${mode}-detector-sets-label-sources-human-basis.png`, fullPage: true });
    await page.getByRole('button', { name: 'Create detector set' }).click();
    await expect(page.getByTestId('set-detail')).toBeVisible();
    const body = seen.find((c) => c.method === 'POST' && c.path === '/api/v1/detector-sets')?.body as {
      roles: Array<{ role: string; label_source_columns?: string[]; negatives_basis?: Record<string, unknown> | null }>;
    };
    const byRole = Object.fromEntries(body.roles.map((r) => [r.role, r]));
    expect(byRole.train.label_source_columns).toEqual(['meanGrade', 'grades']);
    expect(byRole.id_test.label_source_columns).toEqual(['meanGrade', 'grades']);
    expect(byRole.calibration_negatives.negatives_basis).toEqual({
      kind: 'human_labelled',
      label_column: 'human_label',
      negative_values: ['0'],
      labelled_by: 'five Humicroedit graders per edited headline',
      rule: 'meanGrade <= 0.4, or an unedited original',
    });
  });
}

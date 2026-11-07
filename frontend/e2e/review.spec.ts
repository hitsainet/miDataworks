import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { queue, item } from '../src/test/fixtures006';
import { api006 } from './fixtures006';

// Feature 006 (FTASKS 12.7, 12.10): the Review screen, keyboard-driven, in light and dark mode.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`review a queue with the keyboard (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api006(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/review');
    await expect(page.getByRole('heading', { level: 1, name: 'Review' })).toBeVisible();
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await expect(page.getByTestId('miforge-chip')).toContainText('miForge');
    await page.getByTestId('queue-card').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('item-text')).toContainText('cat mayor');
    const focused = page.locator(':focus-visible');
    expect(await focused.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
    await page.screenshot({ path: `e2e/captures/${mode}-review-item.png`, fullPage: true });
    await page.getByTestId('review-panel').focus();
    await page.keyboard.press('a');
    await expect.poll(() => seen.some((c) => c.method === 'POST' && c.path === '/api/v1/review-items/ri_1/decisions')).toBe(true);
    expect(seen.find((c) => c.method === 'POST')?.body).toEqual({ decision: 'accept' });
    await expect(page.getByTestId('item-text')).toContainText('Senate passes budget bill');
    await page.keyboard.press('k');
    await expect(page.getByTestId('item-text')).toContainText('cat mayor');
    await page.getByRole('button', { name: 'Draw an audit' }).click();
    await expect(page.getByLabel('Audit size')).toHaveValue('100');
    await page.screenshot({ path: `e2e/captures/${mode}-review-audit.png`, fullPage: true });
  });
}

// Calibration labels (ENH-006 dry run): side-by-side buttons, one answer at a time, in the same place on every row.
for (const mode of ['dark', 'light'] as const) {
  test(`label a calibration queue with side-by-side buttons (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    const hidden = { model_output_hidden: true, model_snapshot: null };
    const base = api006(seen);
    await stubApi(page, {
      extra: (path, method, url, body) => {
        if (path === '/api/v1/review-queues' && method === 'GET') {
          return { items: [queue({ kind: 'calibration_labeling', show_model_output: false, label_set: ['yes', 'no'], items: 2, question: "Is a number in this text presented as a particular person's Social Security number?" })], total: 1 };
        }
        if (path === '/api/v1/review-queues/rq_1/items') {
          return { items: [item({ ...hidden, text: { text: 'A short row.' } }), item({ ...hidden, id: 'ri_2', position: 1, text: { text: 'A long row. '.repeat(120) } })], total: 2 };
        }
        if (path.endsWith('/decisions') && method === 'POST') {
          seen.push({ method, path, body });
          const id = path.split('/')[4];
          return { status: 201, body: { id: `rd_${id}`, item_id: id, queue_id: 'rq_1', row_key: 'e', decision: 'override', override_label: (body as { override_label: string }).override_label, reason: 'label assigned by the reviewer', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: false, version_id: 'v1', created_at: '2026-11-01T10:00:00Z' } };
        }
        return base(path, method, url, body);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/review');
    await page.getByTestId('queue-card').click();
    await expect(page.getByTestId('item-text')).toContainText('A short row.');
    await expect(page.getByRole('combobox')).toHaveCount(0);
    const yes = page.getByRole('button', { name: 'yes' });
    const no = page.getByRole('button', { name: 'no' });
    const [a, b] = [await yes.boundingBox(), await no.boundingBox()];
    expect(a!.y).toBe(b!.y);
    expect(a!.x + a!.width).toBeLessThanOrEqual(b!.x);
    await expect(page.getByTestId('label-choices').locator('[aria-pressed="true"]')).toHaveCount(0);
    await page.screenshot({ path: `e2e/captures/${mode}-review-choices.png`, fullPage: true });
    await no.click();
    await expect(page.getByTestId('item-text')).toContainText('A long row.');
    // the text sits right under the question, above the buttons
    const [question, text, choices] = await Promise.all([page.getByTestId('queue-view-head').boundingBox(), page.getByTestId('item-text').boundingBox(), page.getByTestId('label-choices').boundingBox()]);
    expect(question!.y + question!.height).toBeLessThanOrEqual(text!.y);
    expect(text!.y + text!.height).toBeLessThanOrEqual(choices!.y);
    await page.getByTestId('review-panel').focus();
    await page.keyboard.press('k');
    await expect(page.getByRole('button', { name: 'no', pressed: true })).toBeVisible();
    await expect(page.getByTestId('label-choices').locator('[aria-pressed="true"]')).toHaveCount(1);
    await page.screenshot({ path: `e2e/captures/${mode}-review-choices-answered.png`, fullPage: true });
    await yes.click();
    await page.keyboard.press('k');
    await expect(page.getByRole('button', { name: 'yes', pressed: true })).toBeVisible();
    await expect(page.getByTestId('label-choices').locator('[aria-pressed="true"]')).toHaveCount(1);
    expect(seen.filter((c) => c.method === 'POST').map((c) => (c.body as { override_label: string }).override_label)).toEqual(['no', 'yes']);
    // a refresh keeps the queue and the row; closing the view stays closed
    await page.keyboard.press('j');
    await expect(page.getByTestId('item-position')).toContainText('Row 2 of 2');
    await page.reload();
    await expect(page.getByTestId('item-position')).toContainText('Row 2 of 2');
    await page.getByTestId('queue-view-head').getByRole('button', { name: 'Close' }).click();
    await expect(page.getByTestId('item-position')).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('queue-card')).toHaveCount(1);
    await expect(page.getByTestId('item-position')).toHaveCount(0);
  });
}

// Rows to change are scattered through a long queue (operator, 2026-11-02): go straight to one by its number.
for (const mode of ['dark', 'light'] as const) {
  test(`go to a row by its number (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    const hidden = { model_output_hidden: true, model_snapshot: null };
    const base = api006(seen);
    await stubApi(page, {
      extra: (path, method, url, body) => {
        if (path === '/api/v1/review-queues' && method === 'GET') {
          return { items: [queue({ kind: 'calibration_labeling', show_model_output: false, label_set: ['yes', 'no'], items: 300 })], total: 1 };
        }
        if (path === '/api/v1/review-queues/rq_1/items') {
          const first = (Number(new URL(String(url), 'http://x').searchParams.get('page')) - 1) * 50 + 1;
          return { items: Array.from({ length: 50 }, (_, i) => item({ ...hidden, id: `ri_${first + i}`, position: first + i - 1, text: { text: `Text of row ${first + i}.` } })), total: 300 };
        }
        if (path.endsWith('/decisions') && method === 'POST') {
          seen.push({ method, path, body });
          const id = path.split('/')[4];
          return { status: 201, body: { id: `rd_${id}`, item_id: id, queue_id: 'rq_1', row_key: 'e', decision: 'override', override_label: (body as { override_label: string }).override_label, reason: 'label assigned by the reviewer', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: false, version_id: 'v1', created_at: '2026-11-02T10:00:00Z' } };
        }
        if (path.endsWith('/decisions')) return [];
        return base(path, method, url, body);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/review');
    await page.getByTestId('queue-card').click();
    await expect(page.getByTestId('item-position')).toContainText('Row 1 of 300');
    const field = page.getByLabel('Go to row');
    await field.fill('301');
    await field.press('Enter');
    await expect(page.getByRole('alert')).toContainText('Enter a row from 1 to 300.');
    await expect(page.getByTestId('item-position')).toContainText('Row 1 of 300');
    await field.fill('197');
    await field.press('Enter');
    await expect(page.getByTestId('item-position')).toContainText('Row 197 of 300');
    await expect(page.getByTestId('item-text')).toContainText('Text of row 197.');
    await expect(page.getByRole('alert')).toHaveCount(0);
    // the panel holds the keys, and draws no ring around the whole screen for it
    await expect(page.getByTestId('review-panel')).toBeFocused();
    expect(await page.getByTestId('review-panel').evaluate((el) => getComputedStyle(el).boxShadow)).not.toMatch(/ [1-9][\d.]*px/);
    await page.screenshot({ path: `e2e/captures/${mode}-review-go-to-row.png`, fullPage: true });
    // the row answers to its key at once, and the answer lands on that row
    await page.keyboard.press('1');
    await expect(page.getByTestId('item-position')).toContainText('Row 198 of 300');
    expect(seen.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/api/v1/review-items/ri_197/decisions']);
    await field.fill('3');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await expect(page.getByTestId('item-text')).toContainText('Text of row 3.');
    await page.reload();
    await expect(page.getByTestId('item-position')).toContainText('Row 3 of 300');
  });
}

// Suite tracker 4.1 (D2): close a queue; closed queues leave the list unless asked for.
for (const mode of ['dark', 'light'] as const) {
  test(`close a queue and show closed queues (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api006(seen) });
    await page.addInitScript(theme, mode);
    page.on('dialog', (d) => void d.accept());
    await page.goto('/#/review');
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await page.getByTestId('queue-card').nth(1).click();
    await page.getByRole('button', { name: 'Close this queue' }).click();
    await expect(page.getByTestId('queue-card')).toHaveCount(1);
    expect(seen.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/api/v1/review-queues/rq_2/close']);
    await page.getByLabel('Show closed queues').check();
    await expect(page.getByTestId('queue-card')).toHaveCount(2);
    await expect(page.getByTestId('queue-card').nth(1)).toContainText('closed');
    await page.screenshot({ path: `e2e/captures/${mode}-review-closed.png`, fullPage: true });
  });
}

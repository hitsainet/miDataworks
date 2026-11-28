import { expect, test, type Locator, type Page } from '@playwright/test';

import { stubApi } from './fixtures';
import { RUN, api007 } from './fixtures007';

// The Generation page's right-hand pane and its filters (operator, 2026-11-27). jsdom has no layout,
// so this is proven here, in both themes:
//
// - a run opened from far down the list shows its detail right below the pinned block, with no
//   scrolling: the pane is sticky under the pinned stack (before, the detail sat at the top of the
//   right-hand column, scrolled out of view, and nothing seemed to happen);
// - the pane is never taller than the window below the pinned block and scrolls inside itself, so a
//   long run form opened there is usable: Tab reaches its last button, and so does the pane's scroll;
// - in one column (a phone) opening a run brings its detail into view below the pinned block;
// - the Status, Behaviour and Sort filters wrap, so none is cut off at 390 px even with the sidebar
//   expanded by hand (134 px of page), and they stay on one line at desktop width.

const ROWS = 40;
const runId = (n: number) => `gr_${String(n).padStart(3, '0')}`;
// Newest first: run 0 is the newest, so it is the first card.
const run = (n: number) => ({
  ...RUN,
  id: runId(n),
  state: n % 4 === 1 ? 'failed' : 'completed',
  room: `dataworks/generation-runs/${runId(n)}`,
  created_at: new Date(Date.UTC(2026, 10, 1, 12, 0) - n * 60_000).toISOString(),
});
const RUNS = Array.from({ length: ROWS }, (_, n) => run(n));
const empty = { items: [], total: 0, page: 1, limit: 50 };

async function open(page: Page, mode: 'light' | 'dark', collapsed = false): Promise<void> {
  const forms = api007();
  await stubApi(page, {
    extra: (path, method, url, body) => {
      if (method === 'GET') {
        if (path === '/api/v1/generation-runs') return { items: RUNS, total: ROWS, page: 1, limit: 50 };
        const one = /^\/api\/v1\/generation-runs\/(gr_\d{3})$/.exec(path);
        if (one) return RUNS.find((r) => r.id === one[1]);
        if (/^\/api\/v1\/generation-runs\/gr_\d{3}\/(records|pairs)$/.test(path)) return empty;
      }
      // The run forms' versions, templates and plan.
      return forms(path, method, url, body);
    },
  });
  await page.addInitScript(([theme, rail]) => {
    window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: rail, mobileOpen: false } }, version: 0 }));
  }, [mode, collapsed] as const);
  await page.goto('/#/generation');
  await expect(page.getByRole('heading', { level: 1, name: 'Generation' })).toBeVisible();
  await expect(page.getByTestId('generation-run-card')).toHaveCount(ROWS);
}

async function box(locator: Locator) {
  const found = await locator.boundingBox();
  if (found === null) throw new Error('no box');
  return found;
}

const scrollTo = (page: Page, at: number | 'end') =>
  page.evaluate((where) => window.scrollTo(0, where === 'end' ? document.documentElement.scrollHeight : where), at);
const scrollY = (page: Page) => page.evaluate(() => window.scrollY);
const pinnedBottom = async (page: Page) => { const b = await box(page.getByTestId('generation-pinned')); return b.y + b.height; };

/** A click where the mouse is, as the operator's: Playwright's own click first scrolls its target into
 *  view below `html`'s scroll padding, which moves the window for a button in the pinned block (it
 *  sits inside that padding) and would hide the very jump these tests look for. */
async function clickInPlace(page: Page, target: Locator) {
  const b = await box(target);
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
}

/** The lowest run card wholly in view below the pinned block. */
async function lowestCardInView(page: Page): Promise<number> {
  const bottom = await pinnedBottom(page);
  const index = await page.getByTestId('generation-run-card').evaluateAll((cards, top) => {
    let found = -1;
    cards.forEach((card, n) => {
      const r = card.getBoundingClientRect();
      if (r.top >= top && r.bottom <= window.innerHeight) found = n;
    });
    return found;
  }, bottom);
  expect(index).toBeGreaterThanOrEqual(0);
  return index;
}

/** `inner` lies wholly inside `outer`, to the half pixel. */
async function expectInside(inner: Locator, outer: Locator, name: string) {
  const i = await box(inner);
  const o = await box(outer);
  expect(i.x, `${name}: left`).toBeGreaterThanOrEqual(o.x - 0.5);
  expect(i.y, `${name}: top`).toBeGreaterThanOrEqual(o.y - 0.5);
  expect(i.x + i.width, `${name}: right`).toBeLessThanOrEqual(o.x + o.width + 0.5);
  expect(i.y + i.height, `${name}: bottom`).toBeLessThanOrEqual(o.y + o.height + 0.5);
}

/** Tabs from `first` until `last` has focus; every stop is wholly in view, inside the pane. */
async function tabThrough(page: Page, first: Locator, last: Locator) {
  const pane = page.getByTestId('generation-pane');
  await first.focus();
  let stops = 0;
  for (; stops < 60; stops += 1) {
    const focused = page.locator(':focus');
    await expect(focused).toHaveCount(1);
    // Retried briefly: Chrome scrolls the focused element into the pane as focus moves.
    await expect(async () => {
      await expect(focused).toBeInViewport({ ratio: 1, timeout: 0 });
      await expectInside(focused, pane, `Tab stop ${stops}`);
      expect((await box(focused)).y).toBeGreaterThanOrEqual(await pinnedBottom(page));
    }).toPass({ timeout: 2000 });
    if (await last.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  expect(stops, 'the last button was reached by Tab').toBeLessThan(60);
  return stops;
}

for (const mode of ['light', 'dark'] as const) {
  test.describe(`${mode} mode`, () => {
    test.use({ viewport: { width: 1280, height: 900 } });

    test('a run opened from far down the list shows its detail below the pinned block, without scrolling', async ({ page }, testInfo) => {
      await open(page, mode);
      const pane = page.getByTestId('generation-pane');
      const detail = page.getByTestId('generation-run-detail');
      // Where the pane sticks (the pinned block sits 16 px lower unscrolled, so measure scrolled).
      await scrollTo(page, 400);
      const start = await box(pane);
      for (const at of ['end', 1500] as const) {
        await scrollTo(page, at);
        const before = await scrollY(page);
        expect(before).toBeGreaterThan(1000);
        const card = await lowestCardInView(page);
        expect(card).toBeGreaterThan(5);
        await clickInPlace(page, page.getByTestId('generation-run-card').nth(card));
        await expect(detail).toBeVisible();
        expect(Math.abs((await scrollY(page)) - before)).toBeLessThanOrEqual(2); // nothing scrolled to show it
        const top = (await box(detail)).y;
        const bottom = await pinnedBottom(page);
        // Right below the pinned block: the detail's top is in view, not behind the block.
        expect(top).toBeGreaterThanOrEqual(bottom);
        expect(top).toBeLessThan(bottom + 40);
        await expect(detail.getByText('Standard run from version v-sft-00')).toBeInViewport({ ratio: 1 });
        expect(Math.abs((await box(pane)).y - start.y)).toBeLessThan(1);
        await expect(page.getByTestId('generation-run-card').nth(card)).toHaveAttribute('aria-pressed', 'true');
      }
      await page.screenshot({ path: testInfo.outputPath(`${mode}-generation-pane-far-down.png`) });
    });

    for (const [button, testId, last] of [
      ['New standard run', 'new-standard-run', /^Try 1 prompt$/],
      ['New steered-pair run', 'new-steered-run', /^Check judge independence$/],
    ] as const) {
      test(`"${button}" opened far down a short window (1280x500): the pane scrolls and its last button is reached`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width: 1280, height: 500 });
        await open(page, mode);
        await scrollTo(page, 'end');
        const before = await scrollY(page);
        await clickInPlace(page, page.getByRole('button', { name: button }));
        const form = page.getByTestId(testId);
        const pane = page.getByTestId('generation-pane');
        await expect(form).toBeVisible();
        expect(Math.abs((await scrollY(page)) - before)).toBeLessThanOrEqual(2);
        if (testId === 'new-standard-run') {
          await form.getByLabel('Input version').selectOption('v-sft-0001');
          await form.getByRole('button', { name: 'Check the plan' }).click();
          await expect(form.getByTestId('plan-summary')).toBeVisible();
          await form.getByLabel('Prompts to try (one per line, up to 5)').fill('a prompt');
        } else {
          // The axis line is filled in by a request after the form opens; let it land before measuring.
          await expect(form.getByTestId('axis-diff')).toContainText('A steered pair must differ');
        }
        // The form is longer than the room below the pinned block, so the pane scrolls inside itself
        // and ends inside the window.
        const sizes = await pane.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }));
        expect(sizes.scroll).toBeGreaterThan(sizes.client + 100);
        const p = await box(pane);
        expect(p.y).toBeGreaterThanOrEqual(await pinnedBottom(page));
        expect(p.y + p.height).toBeLessThanOrEqual(500);
        await page.screenshot({ path: testInfo.outputPath(`${mode}-${testId}-top.png`) });

        // Tab from the first field reaches the last button; every stop is in view inside the pane.
        const lastButton = form.getByRole('button', { name: last });
        await expect(lastButton).toBeEnabled();
        await pane.evaluate((el) => { el.scrollTop = 0; });
        const stops = await tabThrough(page, form.getByLabel('Input version'), lastButton);
        expect(stops).toBeGreaterThan(3);
        expect(await pane.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
        await page.screenshot({ path: testInfo.outputPath(`${mode}-${testId}-last-button.png`) });

        // And the pane's own scroll: from its top, scrolled to its end, the last button is in view.
        await pane.evaluate((el) => { el.scrollTop = 0; });
        await expect(lastButton).not.toBeInViewport({ ratio: 1 });
        await pane.hover();
        await page.mouse.wheel(0, 5000);
        await expect.poll(() => pane.evaluate((el) => Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight)).toBe(true);
        await expect(lastButton).toBeInViewport({ ratio: 1 });
        await expectInside(lastButton, pane, 'the last button');
      });
    }

    test('the templates library opens in the pane, below the pinned block, with no scrolling', async ({ page }) => {
      await open(page, mode);
      await scrollTo(page, 'end');
      const before = await scrollY(page);
      await clickInPlace(page, page.getByRole('button', { name: 'Templates' }));
      const pane = page.getByTestId('generation-pane');
      await expect(pane.getByRole('button').first()).toBeInViewport({ ratio: 1 });
      expect(Math.abs((await scrollY(page)) - before)).toBeLessThanOrEqual(2);
      expect((await box(pane)).y).toBeGreaterThanOrEqual(await pinnedBottom(page));
    });

    test('in one column (a phone) opening a run brings its detail into view below the pinned block', async ({ page }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await open(page, mode);
      // The detail sits below every run here; opening the first card must still show it.
      await page.getByTestId('generation-run-card').first().click();
      const detail = page.getByTestId('generation-run-detail');
      await expect(detail).toBeVisible();
      await expect.poll(async () => (await box(detail)).y).toBeGreaterThanOrEqual(await pinnedBottom(page));
      await expect(detail.getByText('Standard run from version v-sft-00')).toBeInViewport({ ratio: 1 });
      await page.screenshot({ path: testInfo.outputPath(`${mode}-generation-pane-phone.png`) });
      // Closing it (the open card again) moves nothing.
      await scrollTo(page, 0);
      await page.getByTestId('generation-run-card').first().click();
      await expect(detail).toHaveCount(0);
    });

    test('at 390 px with the sidebar expanded by hand, no filter is cut off', async ({ page }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await open(page, mode);
      const toggle = page.getByTestId('sidebar-toggle');
      if ((await page.getByTestId('sidebar').getAttribute('data-collapsed')) === 'true') await toggle.click();
      await expect.poll(async () => (await box(page.getByTestId('sidebar'))).width).toBe(224);
      const controls = page.getByTestId('generation-list-controls');
      const filters = page.getByTestId('generation-run-filters');
      const right = await page.evaluate(() => document.documentElement.clientWidth);
      for (const id of ['filter-state', 'filter-behaviour', 'sort-runs']) {
        const select = page.getByTestId(id);
        await select.scrollIntoViewIfNeeded();
        await expectInside(select, filters, id);
        await expectInside(select, controls, id);
        const b = await box(select);
        expect(b.x + b.width, `${id} inside the window`).toBeLessThanOrEqual(right + 0.5);
        expect(b.x, `${id} right of the sidebar`).toBeGreaterThanOrEqual(224);
        // Its label stays with it, and its text is not cut off inside it.
        expect(await select.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      }
      // Nothing runs past the controls' own right edge either.
      expect(await controls.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`${mode}-filters-390-expanded.png`) });
    });

    test('at desktop width the filters stay on one line', async ({ page }) => {
      await open(page, mode);
      const tops = await Promise.all(['show-archived-generation-runs', 'filter-state', 'filter-behaviour', 'sort-runs'].map(async (id) => {
        const b = await box(page.getByTestId(id));
        return Math.round(b.y + b.height / 2);
      }));
      for (const top of tops) expect(Math.abs(top - tops[0])).toBeLessThanOrEqual(2);
    });
  });
}

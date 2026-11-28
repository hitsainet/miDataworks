import { expect, test, type Page } from '@playwright/test';

import { stubApi } from './fixtures';
import { RUN } from './fixtures007';

// The Generation page's pinned top (operator request 2026-11-27: "Please pin the top of the
// Generation page from the Sort box up"). jsdom has no layout, so the pinning is proven here, in
// both themes:
//
// - every control from the heading down to Sort stays at the same place while the window scrolls
//   the runs, on an opaque block nothing is painted over;
// - the block sits right under the top bar with nothing waiting, and right under the approvals
//   banner's dock when it is open or collapsed, never over it;
// - a run card reached by Tab or Shift+Tab is scrolled into view below the pinned block;
// - on a phone (390 px) and a short window (600 px) the block is capped, so the runs keep about 40%
//   of the viewport, and the part past the cap is reachable by the block's own scroll;
// - the right-hand detail pane still opens a run and closes it.

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

const PENDING = {
  id: 'apr_gen1', action: 'hub_push', target: 'src.api.v1.endpoints.publishing.create_publish',
  summary: 'Publish ver_1 to owner/humor (private)', payload: {}, request_digest: 'abc',
  requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 23 * 3600_000).toISOString(), decided_by: null,
  created_at: new Date().toISOString(),
};

const empty = { items: [], total: 0, page: 1, limit: 50 };

async function open(page: Page, mode: 'light' | 'dark', waiting = false): Promise<void> {
  await stubApi(page, {
    extra: (path, method) => {
      if (method !== 'GET') return undefined;
      if (path === '/api/v1/generation-runs') return { items: RUNS, total: ROWS, page: 1, limit: 50 };
      const one = /^\/api\/v1\/generation-runs\/([^/]+)$/.exec(path);
      if (one) return RUNS.find((r) => r.id === one[1]);
      if (/^\/api\/v1\/generation-runs\/[^/]+\/(records|pairs)$/.test(path)) return empty;
      return undefined;
    },
  });
  // Registered after stubApi, so it answers first: one agent request waiting, or none.
  await page.route('**/api/v1/approvals**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approvals: waiting ? [PENDING] : [] }) }),
  );
  await page.addInitScript((theme) => {
    window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    window.sessionStorage.removeItem('midataworks.approvals-banner.collapsed');
  }, mode);
  await page.goto('/#/generation');
  await expect(page.getByRole('heading', { level: 1, name: 'Generation' })).toBeVisible();
  await expect(page.getByTestId('generation-run-card')).toHaveCount(ROWS);
  if (waiting) await expect(page.getByTestId('approvals-banner')).toBeVisible();
}

async function box(page: Page, testId: string) {
  const found = await page.getByTestId(testId).boundingBox();
  if (found === null) throw new Error(`${testId} has no box`);
  return found;
}

const scrollTo = (page: Page, at: number | 'end') =>
  page.evaluate((where) => window.scrollTo(0, where === 'end' ? document.documentElement.scrollHeight : where), at);

/** Every control the operator asked to keep, from the heading down to Sort. */
const controls = (page: Page) => {
  const pinned = page.getByTestId('generation-pinned');
  return {
    title: pinned.getByRole('heading', { level: 1, name: 'Generation' }),
    subtitle: pinned.getByText('Synthetic rows and steered pairs generated through an endpoint.'),
    help: pinned.getByTestId('help-link-generation'),
    manual: pinned.getByTestId('manual-link-generation'),
    standard: pinned.getByRole('button', { name: 'New standard run' }),
    steered: pinned.getByRole('button', { name: 'New steered-pair run' }),
    templates: pinned.getByRole('button', { name: 'Templates' }),
    archived: pinned.getByTestId('show-archived-generation-runs'),
    status: pinned.getByTestId('filter-state'),
    behaviour: pinned.getByTestId('filter-behaviour'),
    sort: pinned.getByTestId('sort-runs'),
  };
};

async function controlTops(page: Page): Promise<Record<string, number>> {
  const tops: Record<string, number> = {};
  for (const [name, locator] of Object.entries(controls(page))) {
    await expect(locator, name).toBeInViewport({ ratio: 1 });
    const found = await locator.boundingBox();
    if (found === null) throw new Error(`${name} has no box`);
    tops[name] = Math.round(found.y);
  }
  return tops;
}

/** Nothing is painted over the pinned block: each probe point hits the block itself. */
const isOnTop = (page: Page, testId: string) =>
  page.getByTestId(testId).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return [0.05, 0.5, 0.95].every((fy) =>
      [0.05, 0.5, 0.95].every((fx) => el.contains(document.elementFromPoint(r.x + r.width * fx, r.y + r.height * fy))),
    );
  });

/** The pinned block's bottom edge, and where it should start (under the top bar and the dock). */
async function stack(page: Page) {
  const pinned = await box(page, 'generation-pinned');
  const topbar = await box(page, 'topbar');
  const dock = (await page.getByTestId('approvals-banner-dock').count()) ? await box(page, 'approvals-banner-dock') : null;
  return { pinned, start: dock ? dock.y + dock.height : topbar.y + topbar.height, bottom: pinned.y + pinned.height };
}

/** The block is stuck where it should be, every control is where it was, and nothing covers it. */
async function expectPinnedAt(page: Page, tops: Record<string, number>) {
  await expect.poll(async () => { const s = await stack(page); return Math.abs(s.pinned.y - s.start); }).toBeLessThan(1);
  expect(await controlTops(page)).toEqual(tops);
  expect(await isOnTop(page, 'generation-pinned')).toBe(true);
}

for (const mode of ['light', 'dark'] as const) {
  test.describe(`${mode} mode`, () => {
    // Tall enough that the block (about 150 px here) fits under its cap with the banner open; the
    // capped cases have their own viewports below.
    test.use({ viewport: { width: 1280, height: 900 } });

    test('every control from the heading to Sort stays put while the runs scroll', async ({ page }, testInfo) => {
      await open(page, mode);
      // Opaque, in this theme's page colour: no run shows through.
      const colours = await page.getByTestId('generation-pinned').evaluate((el) => [getComputedStyle(el).backgroundColor, getComputedStyle(document.body).backgroundColor]);
      expect(colours[0]).toBe(colours[1]);
      expect(colours[0]).toBe(mode === 'dark' ? 'rgb(15, 22, 41)' : 'rgb(248, 250, 252)');

      await scrollTo(page, 400);
      await expect.poll(async () => { const s = await stack(page); return Math.abs(s.pinned.y - s.start); }).toBeLessThan(1);
      expect((await stack(page)).start).toBe(56); // nothing waiting: right under the top bar
      const tops = await controlTops(page);
      for (const at of [1200, 'end'] as const) {
        await scrollTo(page, at);
        await expectPinnedAt(page, tops);
      }
      // The runs did move: the last is in view, and the first has gone under the block.
      await expect(page.getByTestId('generation-run-card').last()).toBeInViewport();
      await expect(page.getByTestId('generation-run-card').first()).not.toBeInViewport();
      await page.screenshot({ path: testInfo.outputPath(`${mode}-generation-pinned-scrolled.png`) });
    });

    test('with a request waiting the block sits below the banner, open and collapsed', async ({ page }, testInfo) => {
      await open(page, mode, true);
      await scrollTo(page, 600);
      const openTops = await controlTops(page);
      const opened = await stack(page);
      expect(opened.start).toBeGreaterThan(56 + 100); // the open dock holds a card
      for (const at of [1500, 'end'] as const) {
        await scrollTo(page, at);
        await expectPinnedAt(page, openTops);
        expect(await isOnTop(page, 'approvals-banner-dock')).toBe(true);
      }
      await page.screenshot({ path: testInfo.outputPath(`${mode}-generation-pinned-banner-open.png`) });

      await page.getByTestId('approvals-banner-toggle').click();
      await expect(page.getByTestId('approvals-banner-toggle')).toHaveText('Expand');
      await expect.poll(async () => (await box(page, 'approvals-banner-dock')).height).toBeLessThan(80);
      await scrollTo(page, 900);
      await expect.poll(async () => { const s = await stack(page); return Math.abs(s.pinned.y - s.start); }).toBeLessThan(1);
      const collapsed = await stack(page);
      expect(collapsed.start).toBeLessThan(opened.start);
      const collapsedTops = await controlTops(page);
      await scrollTo(page, 'end');
      await expectPinnedAt(page, collapsedTops);
      expect(await isOnTop(page, 'approvals-banner-dock')).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`${mode}-generation-pinned-banner-collapsed.png`) });
    });

    test('a run card reached by Tab or Shift+Tab lands below the pinned block', async ({ page }) => {
      await open(page, mode);
      const cards = page.getByTestId('generation-run-card');
      await scrollTo(page, 'end');
      await cards.nth(ROWS - 1).focus();
      for (let n = ROWS - 2; n >= ROWS - 12; n -= 1) {
        await page.keyboard.press('Shift+Tab');
        await expect(cards.nth(n)).toBeFocused();
        const { bottom } = await stack(page);
        await expect.poll(async () => (await cards.nth(n).boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(bottom);
      }
      // Forward: from Sort, Tab goes to the first card, far above, which is scrolled into view below the block.
      await scrollTo(page, 'end');
      await controls(page).sort.focus();
      await page.keyboard.press('Tab');
      await expect(cards.first()).toBeFocused();
      const { bottom } = await stack(page);
      await expect.poll(async () => (await cards.first().boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(bottom);
      for (let n = 1; n <= 4; n += 1) {
        await page.keyboard.press('Tab');
        await expect(cards.nth(n)).toBeFocused();
        await expect(cards.nth(n)).toBeInViewport({ ratio: 1 });
        expect((await cards.nth(n).boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(bottom);
      }
    });

    test('Shift+Tab lands below the pinned block under an open banner too', async ({ page }) => {
      await open(page, mode, true);
      const cards = page.getByTestId('generation-run-card');
      await scrollTo(page, 'end');
      await cards.nth(ROWS - 1).focus();
      for (let n = ROWS - 2; n >= ROWS - 8; n -= 1) {
        await page.keyboard.press('Shift+Tab');
        await expect(cards.nth(n)).toBeFocused();
        const { bottom } = await stack(page);
        await expect.poll(async () => (await cards.nth(n).boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(bottom);
      }
    });

    for (const [label, width, height] of [['a phone (390 px)', 390, 844], ['a short window (1280x600)', 1280, 600]] as const) {
      for (const waiting of [false, true]) {
        test(`on ${label}${waiting ? ' under an open banner' : ''} the runs keep about 40% of the viewport`, async ({ page }, testInfo) => {
          await page.setViewportSize({ width, height });
          await open(page, mode, waiting);
          await scrollTo(page, 'end');
          await expect.poll(async () => { const t = await stack(page); return Math.abs(t.pinned.y - t.start); }).toBeLessThan(1);
          const s = await stack(page);
          // Never more than 60% of the window for the block, and the runs below it keep ~40%.
          expect(s.pinned.height).toBeLessThanOrEqual(0.6 * height + 1);
          expect(height - s.bottom).toBeGreaterThanOrEqual(0.4 * height - 2);
          await expect(page.getByTestId('generation-run-card').last()).toBeInViewport();
          expect(await isOnTop(page, 'generation-pinned')).toBe(true);
          await page.screenshot({ path: testInfo.outputPath(`${mode}-${width}x${height}${waiting ? '-banner' : ''}.png`) });
          // As the block stands, unscrolled, the list controls are in view: past the cap they stick to
          // its bottom edge while the heading part scrolls.
          await expect(controls(page).archived).toBeInViewport();
          // Past the cap, every control is reachable by the block's own scroll, and the block stays put.
          for (const [name, locator] of Object.entries(controls(page))) {
            await locator.scrollIntoViewIfNeeded();
            await expect(locator, name).toBeInViewport();
          }
          await page.getByTestId('generation-pinned').evaluate((el) => { el.scrollTop = el.scrollHeight; });
          await expect(controls(page).sort).toBeInViewport();
          expect(Math.abs((await box(page, 'generation-pinned')).y - s.pinned.y)).toBeLessThan(1);
        });
      }
    }

    test('the right-hand detail pane still opens a run and closes it', async ({ page }) => {
      await open(page, mode);
      await expect(page.getByText('Open a run to see its records and pairs.')).toBeVisible();
      await page.getByTestId('generation-run-card').nth(1).click();
      const detail = page.getByTestId('generation-run-detail');
      await expect(detail).toBeVisible();
      await expect(detail).toBeInViewport();
      // The pane starts below the pinned block, not under it.
      expect((await box(page, 'generation-run-detail')).y).toBeGreaterThanOrEqual((await stack(page)).bottom);
      await page.keyboard.press('Escape');
      await expect(detail).toHaveCount(0);
      await expect(page.getByText('Open a run to see its records and pairs.')).toBeVisible();
    });
  });
}

import { expect, test, type Page } from '@playwright/test';

import { PANELS } from '../src/config/panels';
import { HEALTH, stubApi } from './fixtures';
import { RUN } from './fixtures007';

// The sidebar starts collapsed on a phone (operator, 2026-11-27, as miForge's does). Below Tailwind's
// `sm` breakpoint (640 px) the sidebar loads as the 64 px icon rail, whether or not an "expanded"
// choice is remembered, and the chevron still expands it. At `sm` and wider the remembered choice
// decides, as before. Loading at a phone width writes nothing over the remembered choice.
//
// Then every screen in the panel registry (config/panels.ts, the route table) at 390 x 844, with no
// stored choice and with a stored "expanded": the rail shows, the page never scrolls sideways, and no
// control is cut off, in both themes. Before, at 390 px the expanded 224 px sidebar left about 134 px
// of page and Generation's Status, Behaviour and Sort selects ran off the right edge.

/** uiStore's persist key (the app's store cannot load here). */
const PREFS = 'midataworks-ui-preferences';
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };

// Sibling addresses as long as an in-cluster one, so Settings' Other apps rows are measured with them.
const LONG_HEALTH = {
  ...HEALTH,
  dependencies: {
    ...HEALTH.dependencies,
    millm: { ...HEALTH.dependencies.millm, url: 'http://miserver-backend.miserver.svc.cluster.local:8000/api/health' },
    mistudio: { ...HEALTH.dependencies.mistudio, url: 'http://miworkbench-backend.miworkbench.svc.cluster.local:8000/api/health' },
  },
};

const ROWS = 12;
const RUNS = Array.from({ length: ROWS }, (_, n) => ({
  ...RUN,
  id: `gr_${String(n).padStart(3, '0')}`,
  state: n % 3 === 1 ? 'failed' : 'completed',
  created_at: new Date(Date.UTC(2026, 10, 1, 12, 0) - n * 60_000).toISOString(),
}));

/** The stored preferences: a theme and, unless `collapsed` is null, a remembered sidebar choice. */
async function open(page: Page, size: { width: number; height: number }, mode: 'light' | 'dark', collapsed: boolean | null, screen = 'generation') {
  await page.setViewportSize(size);
  await stubApi(page, {
    health: LONG_HEALTH,
    extra: (path, method) => {
      if (method !== 'GET') return undefined;
      if (path === '/api/v1/generation-runs') return { items: RUNS, total: ROWS, page: 1, limit: 50 };
      return undefined;
    },
  });
  await page.addInitScript(([key, theme, rail]) => {
    // Once per page load, so a reload in the test keeps what the app wrote meanwhile.
    if (window.sessionStorage.getItem('prefs-seeded') !== null) return;
    window.sessionStorage.setItem('prefs-seeded', '1');
    const state = rail === null ? { theme } : { theme, sidebar: { collapsed: rail, mobileOpen: false } };
    window.localStorage.setItem(key, JSON.stringify({ state, version: 0 }));
  }, [PREFS, mode, collapsed] as const);
  await page.goto(`/#/${screen}`);
  await expect(page.getByTestId('sidebar')).toBeVisible();
  return page.getByTestId('sidebar');
}

/** The remembered sidebar choice, as stored (undefined: none). */
const stored = (page: Page) =>
  page.evaluate((key) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? undefined : (JSON.parse(raw) as { state: { sidebar?: { collapsed: boolean } } }).state.sidebar?.collapsed;
  }, PREFS);

interface Cut { readonly what: string; readonly why: string }

/** Every control (and every element) that runs off the screen sideways or is cut off by a clipping
 *  ancestor. A region that scrolls sideways is its own bound, and what lies right of its edge is
 *  reached by its scroll; left of its edge nothing is. Vertical scrolling is not "cut off". */
function cutOff(page: Page): Promise<Cut[]> {
  return page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const found: { el: Element; cut: Cut }[] = [];
    const describe = (el: Element) => {
      const id = el.getAttribute('data-testid');
      const label = el.getAttribute('aria-label') ?? (el.textContent ?? '').trim().slice(0, 50);
      return `<${el.tagName.toLowerCase()}${id ? ` ${id}` : ''}> "${label}"`;
    };
    const CONTROL = 'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="tab"], [role="checkbox"], [tabindex]:not([tabindex="-1"])';
    for (const el of document.querySelectorAll('body *')) {
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' || style.position === 'fixed') continue;
      if (el.closest('svg') !== null && el.closest('svg') !== el) continue;
      if (el.classList.contains('sr-only') || el.closest('[data-testid="sidebar"]') !== null) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      let left = r.left;
      let right = r.right;
      let bound = { left: 0, right: viewport, name: 'the screen' };
      let clipped = false;
      for (let up = el.parentElement; up !== null && up !== document.body; up = up.parentElement) {
        const overflowX = getComputedStyle(up).overflowX;
        if (overflowX === 'visible') continue;
        const u = up.getBoundingClientRect();
        if (/auto|scroll/.test(overflowX)) {
          // Reached by the region's sideways scroll on the right, never on the left.
          if (left < u.left - 0.5) found.push({ el, cut: { what: describe(el), why: `starts left of its scroller ${describe(up)}` } });
          left = u.left;
          right = Math.min(right, u.right);
          continue;
        }
        if (left < u.left - 0.5 || right > u.right + 0.5) {
          clipped = true;
          bound = { left: u.left, right: u.right, name: `its clipping box ${describe(up)}` };
          break;
        }
      }
      if (!clipped && (left < -0.5 || right > viewport + 0.5)) clipped = true;
      if (!clipped) continue;
      // Text may be clipped with an ellipsis by design; a control may not be.
      if (!el.matches(CONTROL) && style.textOverflow === 'ellipsis') continue;
      found.push({ el, cut: { what: describe(el), why: `runs past ${bound.name} (${Math.round(r.left)}..${Math.round(r.right)} of ${Math.round(bound.left)}..${Math.round(bound.right)})` } });
    }
    // Report the innermost only: an ancestor flagged because a child widened it says nothing new.
    return found.filter(({ el }) => !found.some((other) => other.el !== el && el.contains(other.el))).map(({ cut }) => cut);
  });
}

for (const mode of ['light', 'dark'] as const) {
  test.describe(`${mode} mode`, () => {
    test('a phone with no stored choice starts collapsed, and the arrow still expands it', async ({ page }) => {
      const sidebar = await open(page, PHONE, mode, null);
      await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
      expect((await sidebar.boundingBox())?.width).toBe(64);
      expect(await stored(page)).toBeUndefined();
      await page.getByRole('button', { name: 'Expand the sidebar' }).click();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
      await expect.poll(async () => (await sidebar.boundingBox())?.width).toBe(224);
      // A toggle by hand is remembered.
      expect(await stored(page)).toBe(false);
    });

    test('a phone with a stored "expanded" still starts collapsed and keeps the stored choice', async ({ page }) => {
      const sidebar = await open(page, PHONE, mode, false);
      await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
      expect((await sidebar.boundingBox())?.width).toBe(64);
      // A write of the preferences for another reason (the theme) keeps the remembered "expanded".
      await page.getByRole('button', { name: /Switch to (light|dark) mode/ }).click();
      expect(await stored(page)).toBe(false);
      await page.getByRole('button', { name: 'Expand the sidebar' }).click();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
      await expect.poll(async () => (await sidebar.boundingBox())?.width).toBe(224);
    });

    test('one pixel under sm is a phone; sm itself keeps the stored choice', async ({ page }) => {
      const sidebar = await open(page, { width: 639, height: 844 }, mode, false);
      await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
      await page.setViewportSize({ width: 640, height: 844 });
      await page.reload();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
      expect((await sidebar.boundingBox())?.width).toBe(224);
    });

    test('a desktop keeps a stored "expanded" and a stored "collapsed", and starts expanded with none', async ({ page }) => {
      const sidebar = await open(page, DESKTOP, mode, false);
      await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
      expect((await sidebar.boundingBox())?.width).toBe(224);
      await page.getByRole('button', { name: 'Collapse the sidebar' }).click();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
      expect(await stored(page)).toBe(true);
      await page.reload();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
      expect((await sidebar.boundingBox())?.width).toBe(64);
      await page.evaluate((key) => window.localStorage.removeItem(key), PREFS);
      await page.reload();
      await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
    });

    for (const collapsed of [null, false] as const) {
      const as = collapsed === null ? 'no stored choice' : 'a stored "expanded"';
      test(`at 390 px with ${as}, every screen shows the rail and nothing is cut off or scrolls sideways`, async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        expect(PANELS.length).toBeGreaterThanOrEqual(15);
        let first = true;
        for (const panel of PANELS) {
          if (first) await open(page, PHONE, mode, collapsed, panel.id);
          else await page.goto(`/#/${panel.id}`);
          first = false;
          await expect(page.getByRole('heading', { level: 1, name: panel.title }), panel.id).toBeVisible();
          await expect(page.getByTestId('sidebar'), panel.id).toHaveAttribute('data-collapsed', 'true');
          expect((await page.getByTestId('sidebar').boundingBox())?.width, panel.id).toBe(64);
          if (panel.id === 'generation') {
            await expect(page.getByTestId('generation-run-card')).toHaveCount(ROWS);
            await expect(page.getByTestId('filter-state')).toBeVisible();
          }
          await page.waitForLoadState('networkidle');
          const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          expect.soft(sideways, `${panel.id}: the page scrolls sideways`).toBeLessThanOrEqual(0);
          expect.soft(await cutOff(page), `${panel.id}: cut off`).toEqual([]);
          if (collapsed === null) await page.screenshot({ path: testInfo.outputPath(`${mode}-390-${panel.id}.png`) });
        }
      });
    }
  });
}

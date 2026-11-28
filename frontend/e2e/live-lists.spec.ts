import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';

import { SOURCE } from '../src/test/fixtures001';
import { run } from '../src/test/fixtures005';
import { CHAIN_DONE, CHAIN_STOPPED } from '../src/test/fixtures009';
import { stubApi } from './fixtures';
import { RUN } from './fixtures007';

// Operator, 2026-11-27: "I still have to refresh the miDataworks Generation page to pick up new
// jobs. After I do, the updates are stable, but the first pickup requires a manual refresh."
//
// Each page here is opened with only finished work on it; then a run is created behind the page's
// back, as an agent does through MCP, by changing what the stubbed API answers. The new run must
// appear within one poll interval (5 s) plus a margin, in the same document: no reload, no
// navigation. Before the fix none of these pages re-read its list once every listed run had ended.
const POLL_MS = 5000;
const WITHIN = POLL_MS + 2500;

async function openWithoutReload(page: Page, hash: string): Promise<() => Promise<void>> {
  let loads = 0;
  page.on('load', () => (loads += 1));
  await page.goto(hash);
  await page.evaluate(() => ((window as unknown as { __sameDocument: boolean }).__sameDocument = true));
  return async () => {
    expect(loads).toBe(1);
    expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  };
}

test('generation: a run started elsewhere appears without a reload', async ({ page }) => {
  const runs: unknown[] = [RUN]; // completed
  let listReads = 0;
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/generation-runs' && method === 'GET') {
        listReads += 1;
        return { items: [...runs], total: runs.length, page: 1, limit: 50 };
      }
      if (path === '/api/v1/generation-templates') return { items: [], total: 0, page: 1, limit: 50 };
      return undefined;
    },
  });
  const sameDocument = await openWithoutReload(page, '/#/generation');
  const cards = page.getByTestId('generation-run-card');
  await expect(cards).toHaveCount(1);
  const before = listReads;
  runs.unshift({ ...RUN, id: 'gr_agent', state: 'queued', started_by: 'agent:dataworks-mcp', started_by_origin: 'agent', created_at: '2026-10-08T09:00:00Z', completed_at: null, counts: {}, current_job_id: 'job_agent' });
  await expect(cards).toHaveCount(2, { timeout: WITHIN });
  await expect(cards.first()).toContainText('Queued');
  expect(listReads).toBeGreaterThan(before);
  await sameDocument();
});

test('label runs: a run started elsewhere appears without a reload', async ({ page }) => {
  const runs: unknown[] = [run({ state: 'completed', rows_done: 25000, completed_at: '2026-10-07T13:00:00Z' })];
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/label-runs' && method === 'GET') return { items: [...runs], total: runs.length, page: 1, limit: 50 };
      if (path === '/api/v1/rubrics') return [];
      return undefined;
    },
  });
  const sameDocument = await openWithoutReload(page, '/#/label-runs');
  const cards = page.getByTestId('label-run-card');
  await expect(cards).toHaveCount(1);
  runs.unshift(run({ id: 'lr_agent', state: 'queued', rows_done: 0, started_at: null }));
  await expect(cards).toHaveCount(2, { timeout: WITHIN });
  await expect(cards.first().getByTestId('run-times')).toContainText('not started yet');
  await sameDocument();
});

test('detector sets: a minimal-pair chain started elsewhere appears without a reload', async ({ page }) => {
  const chains: unknown[] = [CHAIN_DONE];
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/minimal-pair-chains' && method === 'GET') return { items: [...chains], total: chains.length };
      return undefined;
    },
  });
  const sameDocument = await openWithoutReload(page, '/#/detector-sets');
  await expect(page.getByTestId(`chain-${CHAIN_DONE.id}`)).toBeVisible();
  chains.unshift({ ...CHAIN_STOPPED, id: 'mpc_agent', state: 'running', failed_stage: null, error: null, resumable: false });
  await expect(page.getByTestId('chain-mpc_agent')).toBeVisible({ timeout: WITHIN });
  await sameDocument();
});

test('datasets: an import started elsewhere appears without a reload', async ({ page }) => {
  const sources: unknown[] = [SOURCE];
  const imported = { ...SOURCE, id: '33333333-3333-3333-3333-333333333333', state: 'importing', display_name: 'agent/imported-dataset', repo_id: 'agent/imported-dataset' };
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/sources' && method === 'GET') return { items: [...sources], total: sources.length, page: 1, limit: 100 };
      if (path === '/api/v1/datasets') return { items: [], total: 0, page: 1, limit: 50 };
      return undefined;
    },
  });
  const sameDocument = await openWithoutReload(page, '/#/datasets');
  // Every source is ready, so the list is folded shut; it opens when a source is importing.
  await expect(page.getByTestId('sources-list')).toContainText('Sources (1 source)');
  sources.push(imported);
  await expect(page.getByTestId('sources-list')).toContainText('Sources (2 sources)', { timeout: WITHIN });
  await expect(page.getByRole('button', { name: `Open source ${imported.display_name}` })).toBeVisible({ timeout: WITHIN });
  await sameDocument();
});

// 2026-11-27, after the mirror's red run 45995522770 (shell.spec.ts "focus rings", dark): a poll
// must never take keyboard focus away. On each polled page an element inside the polled list is
// focused from the keyboard and marked; the stub then answers with changed content (a new run
// first, the focused run's own figures moved) for two polls. The same node must still hold focus,
// with its ring: a re-mounted node would have lost the marker and focus would sit on the body.
async function keepsFocus(page: Page, target: Locator, reads: () => number, change: () => void) {
  await page.keyboard.press('Tab'); // keyboard modality, so programmatic focus shows its ring
  await target.focus();
  await target.evaluate((el) => el.setAttribute('data-focus-marker', 'kept'));
  const focused = page.locator(':focus-visible');
  await expect(focused).toHaveAttribute('data-focus-marker', 'kept');
  change();
  const before = reads();
  await expect.poll(reads, { timeout: 2 * POLL_MS + 3000 }).toBeGreaterThanOrEqual(before + 2);
  await expect(focused).toHaveCount(1);
  await expect(focused).toHaveAttribute('data-focus-marker', 'kept');
  expect(await focused.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe('none');
}

test('generation: a poll keeps keyboard focus on the focused run card', async ({ page }) => {
  const runs: Array<Record<string, unknown>> = [{ ...RUN, state: 'running', completed_at: null }];
  let reads = 0;
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/generation-runs' && method === 'GET') {
        reads += 1;
        return { items: runs.map((r) => ({ ...r })), total: runs.length, page: 1, limit: 50 };
      }
      if (path === '/api/v1/generation-templates') return { items: [], total: 0, page: 1, limit: 50 };
      return undefined;
    },
  });
  await page.goto('/#/generation');
  const card = page.getByTestId('generation-run-card');
  await expect(card).toHaveCount(1);
  await keepsFocus(page, card.first(), () => reads, () => {
    runs[0] = { ...runs[0], counts: { 'respond:generated': 1900, 'respond:discarded': 200, pairs: 0 } };
    runs.unshift({ ...RUN, id: 'gr_agent', state: 'queued', created_at: '2026-10-08T09:00:00Z', completed_at: null, counts: {} });
  });
});

test('label runs: a poll keeps keyboard focus on the focused run card', async ({ page }) => {
  const runs: unknown[] = [run({ rows_done: 6 })];
  let reads = 0;
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/label-runs' && method === 'GET') {
        reads += 1;
        return { items: [...runs], total: runs.length, page: 1, limit: 50 };
      }
      if (path === '/api/v1/rubrics') return [];
      return undefined;
    },
  });
  await page.goto('/#/label-runs');
  const card = page.getByTestId('label-run-card');
  await expect(card).toHaveCount(1);
  await keepsFocus(page, card.first(), () => reads, () => {
    runs[0] = run({ rows_done: 900 });
    runs.unshift(run({ id: 'lr_agent', state: 'queued', rows_done: 0, started_at: null }));
  });
});

test('detector sets: a poll keeps keyboard focus on a chain button', async ({ page }) => {
  const runningChain = { ...CHAIN_STOPPED, state: 'running', failed_stage: null, error: null, resumable: false };
  const chains: unknown[] = [runningChain];
  let reads = 0;
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/minimal-pair-chains' && method === 'GET') {
        reads += 1;
        return { items: [...chains], total: chains.length };
      }
      return undefined;
    },
  });
  await page.goto('/#/detector-sets');
  const cancel = page.getByTestId(`chain-${CHAIN_STOPPED.id}`).getByRole('button', { name: 'Cancel' });
  await expect(cancel).toBeVisible();
  await keepsFocus(page, cancel, () => reads, () => {
    chains[0] = { ...runningChain, stage: 'pair', updated_at: '2026-10-07T01:06:00Z' }; // the fixture already stands at judge
    chains.unshift({ ...runningChain, id: 'mpc_agent' });
  });
});

test('datasets: a poll keeps keyboard focus on a source', async ({ page }) => {
  const importing = { ...SOURCE, id: '44444444-4444-4444-4444-444444444444', state: 'importing', display_name: 'agent/still-importing', repo_id: 'agent/still-importing' };
  const sources: unknown[] = [SOURCE, importing]; // one importing, so the list stays unfolded
  let reads = 0;
  await stubApi(page, {
    extra: (path, method) => {
      if (path === '/api/v1/sources' && method === 'GET') {
        reads += 1;
        return { items: [...sources], total: sources.length, page: 1, limit: 100 };
      }
      if (path === '/api/v1/datasets') return { items: [], total: 0, page: 1, limit: 50 };
      return undefined;
    },
  });
  await page.goto('/#/datasets');
  const open = page.getByRole('button', { name: `Open source ${SOURCE.display_name}` });
  await expect(open).toBeVisible();
  await keepsFocus(page, open, () => reads, () => {
    sources[0] = { ...SOURCE, rows: 200001 };
    sources.push({ ...SOURCE, id: '55555555-5555-5555-5555-555555555555', state: 'importing', display_name: 'agent/new-import', repo_id: 'agent/new-import' });
  });
});

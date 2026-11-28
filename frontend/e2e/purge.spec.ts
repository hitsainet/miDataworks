import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';

// Purge (operator decision 2026-10-20) on the Datasets screen's archived view, in a real browser
// against the built app: select all, deselect one, confirm, and exactly the remaining selected
// datasets are requested and leave the list; select all toggles; a blocked dataset is never sent.

type Item = { kind: string; id: string };

const ARCHIVED = { archived_at: '2026-10-20T14:05:00Z', archived_by: 'Ada', archive_reason: 'e2e data' };
const dataset = (id: string, name: string, archived: boolean) => ({
  id, name, target_type: 'detector', description: null, head_number: 1, head_version_id: `v-${id}`, parent_version_id: null,
  versions: 1, rows: 600, bytes: 52_000, warnings_count: 0, state: 'ready', created_by: 'Ada', created_at: '',
  ...(archived ? ARCHIVED : { archived_at: null, archived_by: null, archive_reason: null }),
});
const ALL = [
  dataset('d-1', 'e2e174-jokes-600b', true),
  dataset('d-2', 'e2e174-p07-3000', true),
  dataset('d-3', 'novice-humor-2026-10-19', true),
  dataset('d-4', 'ops-8-11-realhub-20261018', true),
  dataset('d-5', 'd57-gate3-jokes', false),
];
const BLOCKED: Record<string, string> = {
  'd-4': 'ops-8-11-realhub-20261018 cannot be deleted: publish record pub_8f0 still uses this version.',
};

for (const mode of ['dark', 'light'] as const) {
  test(`${mode}: select all, deselect one, delete permanently`, async ({ page }) => {
    const gone = new Set<string>();
    const purgeRequests: Item[][] = [];
    const outcome = (i: Item, status: string) => ({
      kind: i.kind, id: i.id, name: ALL.find((d) => d.id === i.id)?.name ?? i.id, status,
      code: status === 'blocked' ? 'purge_blocked' : null, message: status === 'blocked' ? BLOCKED[i.id] : null,
      blockers: [], counts: status === 'blocked' ? {} : { versions: 1, labels: 600, label_runs: 1 }, bytes: status === 'blocked' ? 0 : 52_000,
      paths: 2, paths_left: [], tombstone_id: status === 'purged' ? `prg_${i.id}` : null,
    });
    await stubApi(page, {
      extra: (path, method, url, body) => {
        if (path === '/api/v1/datasets/meta') return { target_types: [], defaults: {}, guided_steps: [] };
        if (path === '/api/v1/datasets' && method === 'GET') {
          const archived = url.searchParams.get('include_archived') === 'true';
          const items = ALL.filter((d) => !gone.has(d.id) && (archived || !d.archived_at));
          return { items, total: items.length, page: 1, limit: 200 };
        }
        if (path === '/api/v1/purge/plan') {
          const items = (body as { items: Item[] }).items.map((i) => outcome(i, BLOCKED[i.id] ? 'blocked' : 'purgeable'));
          return { items, purged: 0, blocked: items.filter((i) => i.status === 'blocked').length, bytes: 0 };
        }
        if (path === '/api/v1/purge') {
          const asked = (body as { items: Item[] }).items;
          purgeRequests.push(asked);
          const items = asked.map((i) => outcome(i, BLOCKED[i.id] ? 'blocked' : 'purged'));
          items.filter((i) => i.status === 'purged').forEach((i) => gone.add(i.id));
          return { items, purged: items.filter((i) => i.status === 'purged').length, blocked: 0, bytes: 0 };
        }
        return undefined;
      },
    });
    await page.addInitScript((theme) => {
      window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    }, mode);

    await page.goto('/#/datasets');
    await expect(page.getByTestId('dataset-card')).toHaveCount(1);
    await expect(page.getByTestId('purge-bar')).toHaveCount(0);
    await page.getByTestId('show-archived-datasets').check();
    await expect(page.getByTestId('dataset-card')).toHaveCount(5);
    const bar = page.getByTestId('purge-bar');
    await expect(bar).toBeVisible();
    await expect(page.getByTestId('purge-checkbox')).toHaveCount(4); // archived only
    // The blocked dataset shows why and cannot be selected.
    await expect(page.getByTestId('purge-blocker')).toContainText('publish record pub_8f0');
    await expect(page.getByRole('checkbox', { name: 'dataset ops-8-11-realhub-20261018 cannot be deleted permanently' })).toBeDisabled();
    await expect(page.getByTestId('purge-count')).toHaveText('0 selected · 1 cannot be deleted');

    // Select all toggles: on, off, on.
    const all = page.getByTestId('purge-select-all');
    await all.check();
    await expect(page.getByTestId('purge-count')).toHaveText('3 selected · 1 cannot be deleted');
    await all.uncheck();
    await expect(page.getByTestId('purge-count')).toHaveText('0 selected · 1 cannot be deleted');
    await all.check();
    // Deselect one: the header turns indeterminate.
    await page.getByRole('checkbox', { name: 'Select dataset e2e174-p07-3000 for permanent delete' }).uncheck();
    await expect(page.getByTestId('purge-count')).toHaveText('2 selected · 1 cannot be deleted');
    expect(await all.evaluate((el) => (el as HTMLInputElement).indeterminate)).toBe(true);
    await page.screenshot({ path: `e2e/captures/${mode}-purge-selection.png`, fullPage: true });

    await page.getByTestId('purge-open').click();
    const plan = page.getByTestId('purge-plan');
    await expect(plan.getByTestId('purge-plan-item')).toHaveCount(2);
    await expect(plan).toContainText('e2e174-jokes-600b: removes 1 version, 1 label run, 600 labels; 50.8 KB on disk');
    await expect(plan).toContainText('novice-humor-2026-10-19');
    await expect(plan).not.toContainText('e2e174-p07-3000');
    await page.getByTestId('purge-reason').fill('e2e test data, 2026-10-20 cleanup');
    await page.screenshot({ path: `e2e/captures/${mode}-purge-confirm.png` });
    await page.getByTestId('purge-confirm').click();
    await expect(page.getByTestId('purge-results')).toContainText('Deleted 2 of 2');

    expect(purgeRequests).toEqual([[{ kind: 'dataset', id: 'd-1' }, { kind: 'dataset', id: 'd-3' }]]);
    await page.getByTestId('purge-done').click();
    await expect(page.getByTestId('dataset-card')).toHaveCount(3);
    await expect(page.getByText('e2e174-jokes-600b')).toHaveCount(0);
    await expect(page.getByText('novice-humor-2026-10-19')).toHaveCount(0);
    await expect(page.getByText('e2e174-p07-3000')).toBeVisible();
    await expect(page.getByTestId('purge-count')).toHaveText('0 selected · 1 cannot be deleted');
  });
}

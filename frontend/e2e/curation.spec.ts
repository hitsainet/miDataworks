import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api002 } from './fixtures002';

// Feature 004's Version detail slots and the global warning level (FTASKS 13.8), in both colour
// modes: the shortcut tile and warning, status pills with text, the cross-tab and its samples, the
// profile's not-computed figures, leakage, and the cell balancer's preview.
const col = (column: string, figure: number, valid = true) => ({
  column, kind: 'metadata', n_rows: 10914, n_values: 2, classes: ['humorous', 'not_humorous'], chance: 0.5, figure,
  control_mean: valid ? 0.5 : 0.56, control_runs: 5, folds: 5, valid, invalid_reason: valid ? null : 'control high', bins: null,
  per_value: { values: [{ value: 'joke', counts_by_label: { humorous: 4841, not_humorous: 642 } }, { value: 'headline', counts_by_label: { humorous: 616, not_humorous: 4815 } }], other: null, other_values: 0 },
});
const AUDIT = {
  audit: {
    label_column: 'label', label_source: 'labeler', classes: ['humorous', 'not_humorous'], class_counts: { humorous: 5457, not_humorous: 5457 },
    n_rows: 10914, unlabelled_rows: 0, chance: 0.5, columns: [col('format', 0.885), col('id', 0.5)],
    excluded_columns: [{ column: 'label_probability', reason: 'label_derived', source_operator: 'threshold_labeler@1.0.0' }],
    excluded_by_band: { format: { joke: 5958, headline: 5166 } }, sample_seed: 7,
  },
  warnings: [{ version_id: 'v-2', column: 'format', figure: 0.885, chance: 0.5, control_mean: 0.5, level: 10, margin_pp: 10, level_source: 'code_default', level_set_by: null, n_rows: 10914, sample: false,
    message: 'Format predicts the label 88.5% of the time (held-out balanced accuracy; chance is 50%; on 10,914 rows). Humorous is 89% joke and not humorous is 88% headline. A probe can score well here by detecting format. Evaluate on a set where format does not vary, or build the format-balanced version.' }],
  invalid: [],
  level: { margin_pp: 10, source: 'code_default', set_by: null, set_at: null, reason: null },
};
const LEVEL = { effective_margin_pp: 10, source: 'code_default', level: AUDIT.level, history: [] };
const PROFILE = {
  id: 'r-p', kind: 'profile', state: 'completed', operator: 'profile@1.0.0', params: {}, seed: 7, job_id: null, completed_at: '',
  result: {
    n_rows: 300, sample: true,
    figures: {
      lengths: { status: 'computed', n_rows: 300, sample: true, columns: { text: { characters: { unit: 'characters', edges: [0, 40, 80, 120], counts: [100, 150, 50] }, words: { unit: 'words', edges: [0, 8, 16, 24], counts: [90, 160, 50] } } } },
      language: { status: 'not_computed', reason: 'No language-identification operator is allowlisted.', action: 'Allowlist one on the Operators screen.' },
      exact_duplicates: { status: 'computed', n_rows: 300, sample: true, groups: 2, rows: 3 },
    },
  },
};
const LEAKAGE = {
  id: 'r-l', kind: 'leakage', state: 'completed', operator: 'leakage_check@1.0.0', params: {}, seed: 7, job_id: null, completed_at: '',
  result: { sides: ['test', 'train'], exact_pairs: {}, near_pairs: {}, group_pairs: { 'test|train': 1 }, basis: 'lexical', threshold: 0.8, group_column: 'pair_id', n_rows: 10914, pairs_total: 1 },
};
const BALANCER = {
  status: 'done',
  result: {
    counts: { in: 2000, kept: 452, dropped: 1548 },
    report: {
      column: 'format', label_column: 'label', cap: 113, rows_in: 2000, rows_kept: 452, rows_dropped: 1548, excluded_values: [],
      cells: { 'joke|humorous': { value: 'joke', label: 'humorous', before: 887, after: 113 }, 'headline|humorous': { value: 'headline', label: 'humorous', before: 113, after: 113 } },
      extreme_values: [{ column: 'source_label', value: 'news', dominant_label: 'not_humorous', dominant_rows: 254, rows: 255 }],
      reaudit: { ...AUDIT.audit, columns: [col('format', 0.5), col('source_label', 0.71)] },
    },
  },
};

function curation(path: string, method: string): unknown | undefined {
  if (path === '/api/v1/versions/v-2/shortcut-audit') return AUDIT;
  if (path === '/api/v1/datasets/d-1/shortcut-level') return LEVEL;
  if (path === '/api/v1/settings/shortcut-level') return { margin_pp: 10, source: 'code_default', level: AUDIT.level, history: [] };
  if (path === '/api/v1/versions/v-2/shortcut-audit/cells') return { rows: [{ row_key: 'k1', occurrence: 0, label: 'humorous', excerpt: { text: 'Why was the broom late? because it over swept!' } }], seed: 7, total: 30, cell_rows: 4841 };
  if (path === '/api/v1/versions/v-2/profile') return PROFILE;
  if (path === '/api/v1/versions/v-2/leakage') return LEAKAGE;
  if (path === '/api/v1/versions/v-2/leakage/pairs') return { pairs: [{ kind: 'group', side_a: 'train', side_b: 'test', row_key_a: 'a', row_key_b: 'b', statistic: 1, shared: 'h1', excerpt_a: 'Trump plans a wall', excerpt_b: 'Trump plans a party' }], total: 1 };
  if (path === '/api/v1/operators/cell_balancer/1.0.0/preview' && method === 'POST') return { status: 200, body: BALANCER };
  return undefined;
}

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      const base = api002([]);
      await stubApi(page, { extra: (path, method, url, body) => curation(path, method) ?? base(path, method, url, body) });
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    test('version detail shows the audit, cross-tab, profile and leakage, and previews the balancer', async ({ page }) => {
      await page.goto('/#/datasets');
      await page.getByTestId('dataset-card').click();
      await expect(page.getByTestId('shortcut-tile')).toContainText('88.5%');
      await expect(page.getByText('Warns', { exact: true })).toBeVisible();
      await expect(page.getByText('Clear', { exact: true })).toBeVisible();
      await expect(page.getByText('Excluded by the labeler')).toBeVisible();
      await expect(page.getByText('Language: not computed')).toBeVisible();
      await expect(page.getByText(/1 pair\(s\) cross between splits/)).toBeVisible();
      await page.screenshot({ path: `e2e/captures/${mode}-curation-audit.png`, fullPage: true });

      await page.getByRole('button', { name: 'Samples for joke and humorous' }).click();
      await expect(page.getByText('Why was the broom late? because it over swept!')).toBeVisible();

      await page.getByRole('button', { name: 'Build format-balanced version' }).first().click();
      const dialog = page.getByRole('dialog', { name: 'Build format-balanced version' });
      await expect(dialog).toContainText('Cap 113 per cell');
      await expect(dialog).toContainText('Add a metadata value filter on source_label');
      await dialog.screenshot({ path: `e2e/captures/${mode}-cell-balancer.png` });

      await page.getByRole('button', { name: 'Close the preview' }).focus();
      await page.keyboard.press('Tab');
      const ring = await page.locator(':focus-visible').evaluate((el) => getComputedStyle(el).boxShadow);
      expect(ring).not.toBe('none');
    });

    test('settings shows the global warning level', async ({ page }) => {
      await page.goto('/#/settings');
      await expect(page.getByRole('heading', { name: 'Shortcut warning level' })).toBeVisible();
      await expect(page.getByText(/the default \(10 points, P-19\)/)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Save the level' })).toBeDisabled();
    });
  });
}

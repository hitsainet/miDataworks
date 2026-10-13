import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import type { Extra } from './fixtures';

// Feature 001 on the Datasets screen (task 11.11): preview, import, upload and the source drawer, in
// both colour modes, against route stubs (no backend). Captures are saved for review.
const COMMIT = '2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac';
const SOURCE = {
  id: '11111111-1111-1111-1111-111111111111', kind: 'hf', state: 'ready', display_name: 'CreativeLang/ColBERT_Humor_Detection',
  repo_id: 'CreativeLang/ColBERT_Humor_Detection', config: null, split_selection: null, requested_ref: 'main', resolved_commit: COMMIT,
  content_hash: null, licence_display: 'cc-by-2.0', licence_origin: 'card_data', gated: 'false', token_tier: 'none', rows: 200000,
  splits: ['train'], suggested_target: 'detector', import_job_id: 'job_1', created_by: 'Ada', created_at: '2026-10-06T12:00:00Z', ready_at: '2026-10-06T12:05:00Z',
};
const UPLOAD = { ...SOURCE, id: '22222222-2222-2222-2222-222222222222', kind: 'upload', state: 'importing', display_name: 'jokes.jsonl', repo_id: null, resolved_commit: null, content_hash: 'ab'.repeat(32), licence_display: 'not stated', rows: 0, splits: [], suggested_target: null };
const DETECTION = { detector_version: 'dw.detect/v1', trl_type: 'none', trl_format: null, chat_format: 'plain_text', text_columns: ['text'], label_columns: ['humor'], suggested_target: 'detector', reasons: [{ output: 'trl_type', reason: 'Columns text and humor: plain rows with a label.' }] };
const DETAIL = {
  ...SOURCE,
  files: [{ split: 'train', path: 'sources/x/train.parquet', rows: 200000, bytes: 10920997, sha256: 'cd'.repeat(32), columns: [{ name: 'text', type: 'string' }, { name: 'humor', type: 'bool' }], original_name: null, original_sha256: null }],
  licence: { raw: 'cc-by-2.0', display: 'cc-by-2.0', origin: 'card_data', gated: 'false', redistribution: null, terms_status: 'not recorded', history: [] },
  detection: DETECTION, library_versions: {}, error: null, deleted_by: null, deleted_at: null,
};
const PREVIEW = {
  repo_id: SOURCE.repo_id, requested_ref: null, resolved_commit: COMMIT, head_commit: COMMIT, viewer_commit_note: null, configs: ['default'], config: 'default',
  splits: [{ name: 'train', rows: 200000, bytes: 10920997 }], split: null, columns: [{ name: 'text', type: 'string' }, { name: 'humor', type: 'bool' }],
  sample_rows: [{ text: 'What do you call a pig that took a tour? A hambassador.', humor: true }, { text: 'The meeting moved to Tuesday.', humor: false }],
  licence: { raw: 'cc-by-2.0', display: 'cc-by-2.0', origin: 'card_data' }, gated: 'false', size: { num_rows: 200000, num_bytes_parquet_files: 10920997 }, detection: DETECTION, unavailable: [],
};

const api001: Extra = (path, method) => {
  if (path === '/api/v1/sources' && method === 'GET') return { items: [SOURCE, UPLOAD], total: 2, page: 1, limit: 100 };
  if (path === '/api/v1/sources/hf/preview') return PREVIEW;
  if (path === '/api/v1/sources/hf') return { status: 202, body: { job_id: 'job_7', source_id: null, existing_job: false } };
  if (path === '/api/v1/sources/uploads') return { status: 202, body: { job_id: 'job_8', source_id: null, existing_job: false } };
  if (path === `/api/v1/sources/${SOURCE.id}`) return DETAIL;
  if (path.startsWith('/api/v1/jobs/job_')) return { status: 200, body: { id: path.split('/').pop(), kind: 'source_import', status: 'running', progress: 35, message: 'downloading', params: {}, result: null, error: null, started_by: 'Ada', started_by_origin: 'operator', required_model_id: null, queue_reason: null, heartbeat_at: null, cancel_requested_at: null, started_at: '2026-10-06T12:00:00Z', completed_at: null, dismissed_at: null, created_at: '2026-10-06T12:00:00Z', room: 'x' } };
  if (path === '/api/v1/datasets') return { items: [], total: 0, page: 1, limit: 50 };
  return undefined;
};

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page, { extra: api001 });
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    test('preview, import, upload and the source drawer', async ({ page }) => {
      await page.goto('/#/datasets');
      await expect(page.getByRole('heading', { level: 1, name: 'Datasets' })).toBeVisible();
      await expect(page.getByTestId('sources-list')).toContainText('jokes.jsonl');
      await page.screenshot({ path: `e2e/captures/${mode}-sources-datasets.png`, fullPage: true });

      await page.getByLabel('Repository ID').fill(SOURCE.repo_id);
      await page.getByLabel('Access token (optional)').fill('hf_e2e_token');
      await page.getByRole('button', { name: 'Preview' }).click();
      const modal = page.getByTestId('preview-modal');
      await expect(modal.getByTestId('sample-table')).toContainText('hambassador');
      await expect(modal.getByTestId('detection-panel')).toContainText('Columns text and humor');
      await expect(page.getByLabel('Access token (optional)')).toHaveValue('');
      await page.screenshot({ path: `e2e/captures/${mode}-sources-preview.png` });
      await page.getByRole('button', { name: 'Close the preview' }).click();

      await page.getByRole('button', { name: 'Import', exact: true }).click();
      await expect(page.getByTestId('import-progress').first()).toBeVisible();

      await page.getByLabel('Choose files to upload').setInputFiles({ name: 'more-jokes.jsonl', mimeType: 'application/json', buffer: Buffer.from('{"text":"x"}\n') });
      await expect(page.getByLabel('Split name for more-jokes.jsonl')).toHaveValue('train');
      await page.getByRole('button', { name: 'Upload 1 file' }).click();
      await expect(page.getByTestId('import-progress')).toHaveCount(2);
      await page.screenshot({ path: `e2e/captures/${mode}-sources-importing.png`, fullPage: true });

      await page.getByRole('button', { name: `Open source ${SOURCE.display_name}` }).click();
      const drawer = page.getByTestId('source-drawer');
      await expect(drawer).toContainText('Build a version from this source');
      await expect(drawer.getByTestId('detection-panel')).toContainText('detector');
      await page.screenshot({ path: `e2e/captures/${mode}-sources-drawer.png` });
      await drawer.getByRole('button', { name: 'Close the source' }).focus();
      await page.keyboard.press('Tab');
      const ring = await page.locator(':focus-visible').evaluate((el) => getComputedStyle(el).boxShadow);
      expect(ring).not.toBe('none');
    });
  });
}

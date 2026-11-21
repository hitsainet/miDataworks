import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';

// Publish and export (008 FTASKS 11.7): a private publish end to end against a stateful stub of the
// API (the backend's own tests run the same flow on the faithful fake Hub), a public push refused
// on an amber check, in both colour modes, with visible focus rings.

const amber = (check: string, reason: string) => ({ check, outcome: 'amber', reason, next_step: 'Publish privately, or wait for the owning feature.', evidence: { not_checked: true } });
const green = (check: string, reason: string) => ({ check, outcome: 'green', reason, next_step: 'Nothing to do.', evidence: {} });
const RESULTS = [
  green('C-1', 'All 1 source licences permit redistribution.'),
  green('C-2', 'The stored Hugging Face token can write to this namespace.'),
  amber('C-3', 'Near-duplicate leakage into the held-out split has not been checked.'),
  green('C-4', 'No model labelled or generated rows in this version.'),
  amber('C-5', 'The stratified audit sample has not been checked.'),
  green('C-6', 'No model labeler produced labels in this version.'),
  amber('C-7', 'The shortcut audit has not been checked.'),
  { check: 'repository', outcome: 'note', reason: 'mistudio/humor-format-balanced does not exist yet; it will be created private.', next_step: 'Nothing to do.', evidence: { exists: false, effective_visibility: 'private' } },
];
const FILES = [
  { name: 'train', path: 'data/train.parquet', rows: 2208, bytes: 181234, sha256: '89b59b401b20f120366eb9450c01f46468eeb8013a05ec061a922ca115e98209', git_blob_sha1: 'b'.repeat(40), logical_digest: 'c'.repeat(64), label_counts: { humorous: 1104, not_humorous: 1104 }, held_out: false, evaluation_only: false },
  { name: 'test', path: 'data/test.parquet', rows: 244, bytes: 20871, sha256: 'b20a9fb101f6a841c9520e73086c024f55e01e54dfaa907cb2999a2aed186965', git_blob_sha1: 'd'.repeat(40), logical_digest: 'e'.repeat(64), label_counts: { humorous: 122, not_humorous: 122 }, held_out: true, evaluation_only: true },
];

function backend() {
  const state = { published: false, polls: 0 };
  const publishRecord = () => ({
    id: 'pub_1', job_id: 'job_p', version_id: 'v1', build_id: 'pbld_1', kind: 'publish', repo_id: 'mistudio/humor-format-balanced',
    requested_visibility: 'private', visibility_after: 'private', commit: '802d806b0f3c4f1e8a9b7c6d5e4f3a2b1c0d9e8f', status: 'published',
    check_snapshot: RESULTS, started_by: 'Ada', started_by_origin: 'operator', send_id: null, error: null, created_at: '', completed_at: '',
    files: FILES.map((f) => ({ path: f.path, role: 'split', split: f.name, bytes: f.bytes, sha256: f.sha256, git_blob_sha1: f.git_blob_sha1, remote_lfs_sha256: f.sha256, remote_blob_id: null, match: true })),
  });
  const ok = (body: unknown) => ({ status: 200, body });
  const answer = (path: string, method: string): unknown => {
    if (path === '/api/v1/versions') return { items: [{ id: 'v1', dataset_id: 'd1', dataset_name: 'humor-format-balanced', target_type: 'detector', number: 1, state: 'completed', is_head: true }], total: 1 };
    if (path === '/api/v1/publishes' && method === 'GET') return { items: state.published ? [publishRecord()] : [], total: state.published ? 1 : 0 };
    if (path === '/api/v1/exports') return { items: [], total: 0 };
    if (path === '/api/v1/versions/v1/publish-builds') return { build_id: 'pbld_1', job_id: null, reused: true, status: 'completed' };
    if (path === '/api/v1/publish-builds/pbld_1') return { id: 'pbld_1', version_id: 'v1', status: 'completed', projection: {}, files: FILES, columns: [], omitted: {}, error: null, job_id: null, created_at: '', completed_at: '' };
    if (path === '/api/v1/versions/v1/card-draft') return { front_matter: { license: 'cc-by-2.0', configs: [{ config_name: 'default', data_files: [{ split: 'train', path: 'data/train.parquet' }] }] }, record_markdown: '<!-- dw:record — regenerated on every publish -->\n## Record', prose: '# humor-format-balanced v1', build_id: 'pbld_1' };
    if (path === '/api/v1/versions/v1/publish-checks') return { check_run_id: 'pchk_1', job_id: 'job_c' };
    if (path === '/api/v1/publish-check-runs/pchk_1') return { id: 'pchk_1', version_id: 'v1', build_id: 'pbld_1', repo_id: 'mistudio/humor-format-balanced', requested_visibility: 'private', status: 'completed', results: RESULTS, licence_table_version: 1, job_id: 'job_c', created_at: '', completed_at: '' };
    if (path === '/api/v1/jobs/job_c') return { id: 'job_c', status: 'completed', progress: 100, message: null };
    if (path === '/api/v1/publishes' && method === 'POST') return { status: 201, body: { publish_id: 'pub_1', job_id: 'job_p', request_digest: 'f'.repeat(64) } };
    if (path === '/api/v1/jobs/job_p') {
      state.polls += 1;
      if (state.polls > 1) state.published = true;
      return state.published
        ? { id: 'job_p', status: 'completed', progress: 100, message: 'published' }
        : { id: 'job_p', status: 'running', progress: 70, message: 'verifying' };
    }
    return undefined;
  };
  // The fixture reads a top-level `status` as the HTTP status, and these bodies carry their own.
  return (path: string, method: string) => {
    const body = answer(path, method);
    if (body === undefined) return undefined;
    return typeof (body as { status?: unknown }).status === 'number' ? body : ok(body);
  };
}

for (const mode of ['dark', 'light'] as const) {
  test.describe(`${mode} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await stubApi(page, { extra: backend() });
      await page.addInitScript((theme) => {
        window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
      }, mode);
    });

    test('a private publish ends on the verification; public is refused on amber', async ({ page }) => {
      await page.goto('/#/publish');
      await expect(page.getByRole('heading', { level: 1, name: 'Publish and export' })).toBeVisible();
      await page.getByLabel('Version').selectOption('v1');
      await page.getByLabel('Repository').fill('mistudio/humor-format-balanced');
      await page.getByRole('button', { name: 'Preview files' }).click();
      await expect(page.getByTestId('file-preview')).toContainText('2,208');
      await expect(page.getByTestId('file-preview')).toContainText('held out · evaluation only');
      await expect(page.getByTestId('check-C-7')).toContainText('Blocks a public push');

      await page.getByLabel('Visibility').selectOption('public');
      const publicButton = page.getByRole('button', { name: 'Publish publicly' });
      await expect(page.getByTestId('check-C-3')).toBeVisible();
      await expect(publicButton).toBeDisabled();
      await expect(page.getByTestId('publish-blocker')).toHaveAttribute('data-blocker', /C-3/);
      await page.screenshot({ path: `e2e/captures/${mode}-publish-refused.png`, fullPage: true });

      await page.getByLabel('Visibility').selectOption('private');
      const privateButton = page.getByRole('button', { name: 'Publish privately' });
      await expect(privateButton).toBeEnabled();
      await privateButton.click();
      await expect(page.getByTestId('publish-result')).toContainText('every file\'s hash matched', { timeout: 10_000 });
      await expect(page.getByTestId('publish-history')).toContainText('Hashes match');
      await expect(page.getByTestId('publish-history')).toContainText('802d806b');
      await page.screenshot({ path: `e2e/captures/${mode}-publish-done.png`, fullPage: true });

      await page.getByLabel('Repository').focus();
      await page.keyboard.press('Tab');
      const ring = await page.locator(':focus-visible').evaluate((el) => getComputedStyle(el).boxShadow);
      expect(ring).not.toBe('none');
    });
  });
}

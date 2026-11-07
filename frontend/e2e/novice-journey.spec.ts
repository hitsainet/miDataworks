import { expect, test } from '@playwright/test';

import { DETAIL } from '../src/test/fixtures001';
import { META } from '../src/test/fixtures002';
import { CURATE_LIST, DEDUP_EXACT, SAMPLE_OP } from '../src/test/fixtures003';
import { PROBE_LIST, PROBE_PLAN, probeRun } from '../src/test/fixtures005';
import { probeOptionText, probeTags } from '../src/components/labeling/probeChoice';
import type { ProbeListItem } from '../src/types/labeling';
import { HEALTH, stubApi } from './fixtures';

// The novice journey of 001 FTASKS 16.8 and 005 FTASKS 17.6 (criterion 12), the one the walk of
// 2026-10-19 could not finish: a Hugging Face repository → a source → the dataset → Curate with
// "Sample rows" at 25,000 → Build version → Label, where the plan says 25,000 rows.
//
// The backend and miLLM are a stateful fake: the draft keeps what the page saves, the recipe is
// saved from the draft, the build is a job that completes on the second poll, and the plan counts
// what row_filter.max_rows allows of the version's 200,000 rows. Nothing here is a hand-set flag:
// every screen reaches the next one through the buttons a novice would press.
const SOURCE_ROWS = 200_000;
const LLAMA = 'meta-llama/Llama-3.1-8B-Instruct';
const MILLM_UI = 'https://millm.example.test';
// The re-walk's two humor probes (2026-10-19, finding 2): one model, one layer, one concept.
const HUMOR_OLD: ProbeListItem = {
  ...PROBE_LIST.items[0], probe_id: 'pr_7de1e85e2d16', name: 'pmr_59980a0fb155 L12', layer: 12, threshold: 101.25,
  label_mapping: { humorous: 'positive', not_humorous: 'negative' }, concept: null, created_at: '2026-10-01T16:00:00Z',
  trained_at: '2026-09-30T22:05:00Z', train_dataset: 'CreativeLang/ColBERT_Humor_Detection (train)', mistudio_run_id: 'pmr_59980a0fb155',
  auroc: 0.749, auroc_dataset: 'CreativeLang/ColBERT_Humor_Detection (test)', target_fpr: 0.01,
};
const HUMOR_NEW: ProbeListItem = { ...HUMOR_OLD, probe_id: 'pr_a1993af56691', name: 'pmr_a1993af56691 L12', created_at: '2026-10-18T13:00:00Z', trained_at: '2026-10-18T12:00:00Z', mistudio_run_id: 'pmr_a1993af56691', auroc: 0.712 };
const PROBES = [HUMOR_OLD, HUMOR_NEW];

/** The journey's handle on miLLM: the operator loads the model in miLLM's own UI. */
let loadModelInMillm: () => void = () => undefined;

type Seen = Array<{ method: string; path: string; body: unknown }>;

function fakeBackend(seen: Seen) {
  const state = {
    draft: null as null | Record<string, unknown>,
    recipe: null as null | { id: string; name: string; body: unknown },
    jobPolls: 0,
    version: null as null | Record<string, unknown>,
    millmLoaded: false,
    runPolls: 0,
  };
  const version = () => ({
    id: 'v-new', dataset_id: 'd-new', dataset_name: 'colbert-humor-detection', target_type: 'detector', number: 1, state: 'completed',
    is_head: true, superseded_by: null, parent_version_id: null, total_rows: 25_000, total_bytes: 1_700_000, warnings_count: 0,
    recipe_hash: 'c'.repeat(64), seed: 1274078956, created_by: 'Ada', created_at: '2026-10-19T23:47:00Z', request_digest: 'd'.repeat(64),
    inputs: [{ kind: 'source', source_id: DETAIL.id }], recipe_revision_id: 'rev-1', recipe_id: 'rec-1', bindings: [], rowkey_scheme: 'dw.rowkey/v1',
    column_roles: { text: 'content', humor: 'metadata' }, splits: [{ name: 'train', held_out: false, rows: 25_000, bytes: 1_700_000, file_sha256: 'e'.repeat(64), logical_digest: 'f'.repeat(64), path: 'versions/v-new/train.parquet' }],
    held_out_origin_version_id: null, warnings: [], manifest_sha256: '1'.repeat(64), build_job_id: 'job_build', created_by_origin: 'operator',
    drop_summary: [{ step_index: 1, operator: 'sample', operator_version: '1.0.0', reused: false, rows_in: SOURCE_ROWS, rows_out: 25_000, dropped: 175_000, changed: 0, added: 0, split_assigned: 0, reasons: [{ kind: 'dropped', reason_code: 'not_sampled', count: 175_000, example: 'not among the 25,000 of 200,000 rows drawn (seed 1274078956)' }] }],
  });
  const old = Array.from({ length: 120 }, (_, i) => ({ ...version(), id: `old-${i}`, dataset_name: `e2e174-test-${i}`, total_rows: 20 + i, created_at: '2026-10-10T00:00:00Z', drop_summary: [] }));

  loadModelInMillm = () => {
    state.millmLoaded = true;
  };
  return (path: string, method: string, url: URL, body: unknown): unknown => {
    seen.push({ method, path, body });
    const b = (body ?? {}) as Record<string, unknown>;
    if (path === '/api/v1/datasets/meta') return META;
    if (path === '/api/v1/recipe-drafts' && method === 'POST') {
      state.draft = { id: 'draft-n', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], updated_by: 'Ada', updated_at: '', ...b };
      return { status: 201, body: state.draft };
    }
    if (path === '/api/v1/recipe-drafts/draft-n' && method === 'GET') return state.draft;
    if (path === '/api/v1/recipe-drafts/draft-n' && method === 'PUT') {
      state.draft = { ...state.draft, ...b };
      return state.draft;
    }
    if (path === '/api/v1/recipe-drafts/draft-n/save' && method === 'POST') {
      state.recipe = { id: 'rec-1', name: String(b.recipe_name), body: state.draft!.body };
      state.draft = { ...state.draft, recipe_id: 'rec-1' };
      return { status: 201, body: { id: 'rev-1', recipe_id: 'rec-1', revision_number: 1, recipe_hash: 'c'.repeat(64), body: state.recipe.body, step_labels: [], cloned_from_revision_id: null, imported: false, created_by: 'Ada', created_by_origin: 'operator', created_at: '', versions_built: 0 } };
    }
    if (path === '/api/v1/sources/hf' && method === 'POST') return DETAIL; // already imported: the existing source
    if (path === `/api/v1/sources/${DETAIL.id}`) return DETAIL;
    if (path === '/api/v1/datasets' && method === 'POST') return { status: 201, body: { id: 'd-new', name: b.name, target_type: b.target_type, version_list: [] } };
    if (path === '/api/v1/datasets' && method === 'GET') return { items: [], total: 0 };
    if (path === '/api/v1/operators' && method === 'GET') return CURATE_LIST;
    if (path === '/api/v1/operators/sample/1.0.0') return SAMPLE_OP;
    if (path === '/api/v1/operators/dedup_exact/1.0.0') return DEDUP_EXACT;
    if (path === '/api/v1/recipes/validate') {
      // the registry's real rule for sample: the chosen mode's size must be given
      const steps = ((b.body as { steps?: Array<{ operator: string; params: Record<string, unknown> }> }).steps ?? []);
      const results = steps.map((s, i) => ({ index: i + 1, operator: s.operator, version: '1.0.0', errors: s.operator === 'sample' && s.params.count === undefined ? [{ code: 'params_invalid', message: '/count: sampling by count needs a number of rows.' }] : [] }));
      return { valid: results.every((r) => r.errors.length === 0), body_errors: [], steps: results };
    }
    if (path === '/api/v1/recipes/rec-1/build' && method === 'POST') return { status: 202, body: { job_id: 'job_build', existing_job: false, seed: 1274078956, request_digest: 'd'.repeat(64) } };
    if (path === '/api/v1/jobs/job_build') {
      state.jobPolls += 1;
      const done = state.jobPolls >= 2;
      if (done) state.version = version();
      // wrapped: a job body has its own "status" field
      return { status: 200, body: { id: 'job_build', kind: 'version_build', status: done ? 'completed' : 'running', progress: done ? 100 : 40, message: null, params: {}, result: done ? { version_id: 'v-new' } : null, error: null, started_by: 'Ada', started_by_origin: 'operator', required_model_id: null, queue_reason: null, heartbeat_at: null, cancel_requested_at: null, started_at: '', completed_at: null, dismissed_at: null, created_at: '', room: 'dataworks/version-builds/job_build' } };
    }
    if (path === '/api/v1/versions' && method === 'GET') return { items: [...(state.version ? [state.version] : []), ...old], total: old.length + (state.version ? 1 : 0) };
    if (path === '/api/v1/versions/v-new') return state.version ?? { status: 404, body: { error: { code: 'version_not_found', message: 'No version v-new.', details: {} } } };
    if (path.startsWith('/api/v1/versions/v-new/')) return { status: 200, body: null };
    if (path === '/api/v1/decision-templates') return [];
    if (path === '/api/v1/rubrics') return [];
    if (path === '/api/v1/labeling/probes') {
      const fits = state.millmLoaded ? true : null;
      return { ...PROBE_LIST, items: PROBES.map((p) => ({ ...p, fits_resident_model: fits })), resident_model: state.millmLoaded ? LLAMA : null };
    }
    if (path === '/api/v1/label-runs/plan' && !state.millmLoaded) {
      return { status: 409, body: { error: { code: 'MODEL_NOT_LOADED', message: `No model is loaded in miLLM. Load ${LLAMA} (the model probe ${HUMOR_OLD.probe_id} was fitted on), then start the run.`, details: { needed: LLAMA, resident: null } } } };
    }
    if (path === '/api/v1/label-runs' && method === 'POST') {
      return { status: 201, body: probeRun({ id: 'lr_new', state: 'queued', rows_done: 0, rows_total: 25_000, input_version_id: 'v-new', counts: {} }) };
    }
    if (path === '/api/v1/label-runs/lr_new') {
      // the first read finds it part done; the next poll finds it finished
      state.runPolls += 1;
      const done = state.runPolls >= 2;
      return probeRun({
        id: 'lr_new', state: done ? 'completed' : 'running', rows_done: done ? 25_000 : 12_000, rows_total: 25_000, input_version_id: 'v-new',
        input_dataset_name: 'colbert-humor-detection', input_version_number: 1, counts: done ? { positive: 2963, negative: 22037 } : { positive: 1400, negative: 10600 },
        timing: done ? { active_seconds: 1163, jobs_timed: 1, rows_scored: 25_000, rows_per_second: 21.5 } : null,
      });
    }
    if (path === '/api/v1/label-runs/lr_new/agreement') {
      return {
        label_run_id: 'lr_new', run_state: 'completed', applicable: true, reason: null, columns: [{ name: 'humor', values: ['false', 'true'] }],
        column: 'humor', values: ['false', 'true'], positive_value: 'true', needs_positive_value: false, rows_compared: 25_000, rows_not_compared: 0,
        counts: { both_positive: 2500, run_only_positive: 463, column_only_positive: 10_000, both_negative: 12_037 }, accuracy: 0.5815,
        run_positive_share: 0.11852, column_positive_share: 0.5, auroc: 0.748, auroc_from: 'probe score',
        note: "The run marked 11.9% of the 25,000 compared rows positive; the column 'humor' marks 50.0% of them positive. They agree on 58.1%. That is weak agreement: the labeler and the column disagree on many rows.",
      };
    }
    if (path === '/api/v1/label-runs/plan') {
      const request = JSON.parse(url.searchParams.get('request') ?? '{}') as { row_filter?: { max_rows?: number } };
      const rows = Math.min(request.row_filter?.max_rows ?? 25_000, 25_000);
      return { ...PROBE_PLAN, rows_total: rows, rows_to_score: rows, rows_reused: 0, row_coverage: { rows, row_keys: rows, keys_with_copies: 0, rows_in_copied_keys: 0, copies_disagree: null } };
    }
    if (path.startsWith('/api/v1/calibration')) return { status: 404, body: { error: { code: 'not_found', message: 'none', details: {} } } };
    return undefined;
  };
}


for (const mode of ['dark', 'light'] as const) {
  test(`a novice goes from a repository ID to a 25,000-row label plan (${mode})`, async ({ page }) => {
    const seen: Seen = [];
    const health = { ...HEALTH, dependencies: { ...HEALTH.dependencies, millm: { ...HEALTH.dependencies.millm, ui_url: MILLM_UI } } };
    await stubApi(page, { extra: fakeBackend(seen), health });
    await page.addInitScript((theme) => {
      window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));
    }, mode);
    await page.goto('/#/new-dataset');
    const rail = page.getByTestId('step-rail');

    // 1. Import: the repository ID; it is already imported, so the existing source is used, and
    //    the step says so (re-walk finding 8). One click posts one import (finding 5).
    await page.getByLabel('Repository ID').fill('CreativeLang/ColBERT_Humor_Detection');
    await page.getByRole('button', { name: 'Import', exact: true }).click();
    await expect(page.getByTestId('already-imported')).toContainText('Already imported');
    expect(seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/sources/hf')).toHaveLength(1);
    await page.getByRole('button', { name: 'Use this source' }).click();

    // 2. Goal: the dataset name is suggested from the source.
    await expect(page.getByLabel(/Dataset name/)).toHaveValue('colbert-humor-detection');
    await page.getByLabel('Goal').selectOption('detector');
    await page.getByRole('button', { name: 'Create the dataset' }).click();

    // 3. Profile has nothing to read yet, says so, and leads to Curate; the rail does not call it done.
    await expect(page.getByTestId('profile-needs-version')).toBeVisible();
    await page.getByRole('button', { name: 'Go to Curate to build the version' }).click();
    await expect(rail.locator('li[data-state="not done"]')).toHaveCount(1);
    await expect(rail.getByText('not done: needs a built version')).toBeVisible();

    // 4. Curate: Build is disabled and says why; add "Sample rows" and set 25,000.
    await expect(page.getByTestId('build-blockers')).toContainText('Add at least one step on the Curate step');
    await expect(page.getByRole('button', { name: 'Build version' })).toBeDisabled();
    await page.getByRole('button', { name: 'Add Sample rows' }).click();
    await expect(page.getByTestId('curate-check')).toContainText('Some steps have problems');
    await expect(page.getByTestId('step-errors')).toContainText('sampling by count needs a number of rows');
    await page.getByLabel(/Rows to keep/).fill('25000');
    await expect(page.getByTestId('curate-check')).toContainText('The step is valid');
    await expect(page.getByLabel('Recipe name')).toHaveValue('colbert-humor-detection-recipe');
    await page.screenshot({ path: `e2e/captures/${mode}-novice-curate.png`, fullPage: true });

    // 5. Build: saves the recipe from the draft, builds, and records the version on the draft.
    await expect(page.getByRole('button', { name: 'Build version' })).toBeEnabled();
    await page.getByRole('button', { name: 'Build version' }).click();
    await expect(page.getByTestId('build-progress')).toContainText('Built.', { timeout: 15_000 });
    const saved = seen.find((c) => c.path === '/api/v1/recipe-drafts/draft-n/save');
    expect(saved?.body).toEqual({ recipe_name: 'colbert-humor-detection-recipe' });
    const put = seen.filter((c) => c.method === 'PUT' && c.path === '/api/v1/recipe-drafts/draft-n').map((c) => c.body as { body?: { steps?: unknown[] } });
    expect(put.some((d) => JSON.stringify(d.body?.steps) === JSON.stringify([{ operator: 'sample', version: '1.0.0', params: { count: 25000 } }]))).toBe(true);
    expect(seen.find((c) => c.path === '/api/v1/recipes/rec-1/build')?.body).toEqual({ dataset_id: 'd-new', inputs: [{ kind: 'source', source_id: DETAIL.id }] });
    await expect(rail.locator('li[data-state="done"]')).toHaveCount(3); // import, goal, and profile (now it has a version); curate is current
    await page.getByRole('button', { name: 'Continue to labeling', exact: true }).click();

    // 6. Label: the built version is chosen; the 120 older test versions are folded away (finding
    //    11). The two humor probes are told apart: the higher AUROC is recommended (finding 2).
    await expect(page.getByLabel('Version to label')).toHaveValue('v-new');
    await expect(page.getByTestId('toggle-old-versions')).toContainText('Show 120 older and test versions');
    await expect(page.getByLabel('Version to label').locator('option')).toHaveCount(2); // the prompt and v-new
    await page.getByLabel('Label with').selectOption('probe');
    const tags = probeTags(PROBES);
    const unloaded = (p: ProbeListItem) => probeOptionText({ ...p, fits_resident_model: null }, tags[p.probe_id]);
    expect(unloaded(HUMOR_OLD)).toContain('recommended');
    expect(unloaded(HUMOR_NEW)).toContain('newest');
    await page.getByLabel('Probe', { exact: true }).selectOption({ label: unloaded(HUMOR_OLD) });
    await expect(page.getByTestId('probe-facts')).toContainText('AUROC 0.749 on CreativeLang/ColBERT_Humor_Detection (test)');
    await page.getByLabel('Rows to label').fill('25000');

    // Nothing is loaded in miLLM: the step says what to load, links to miLLM's Models page
    // (finding 3), and the blocked button names its count (finding 7).
    await expect(page.getByTestId('model-wait')).toContainText(`Load ${LLAMA} in miLLM.`, { timeout: 10_000 });
    await expect(page.getByTestId('millm-models-link')).toHaveAttribute('href', `${MILLM_UI}/models`);
    await expect(page.getByRole('button', { name: 'Label 25,000 rows' })).toBeDisabled();

    // A reload keeps the labeler, the probe and the cap: they are on the draft (finding 1).
    await expect.poll(() => seen.some((c) => c.method === 'PUT' && JSON.stringify(c.body).includes(`"probe_id":"${HUMOR_OLD.probe_id}"`))).toBe(true);
    await page.reload();
    await expect(page.getByLabel('Label with')).toHaveValue('probe');
    await expect(page.getByLabel('Probe', { exact: true })).toHaveValue(HUMOR_OLD.probe_id);
    await expect(page.getByLabel('Rows to label')).toHaveValue('25000');

    // The operator loads the model in miLLM's own UI and comes back to the tab: the step notices
    // with no reload (finding 1), and the plan says 25,000 rows.
    loadModelInMillm();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page.getByTestId('model-wait')).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByTestId('plan-line')).toContainText('25,000 rows', { timeout: 10_000 });
    await expect(page.getByTestId('probe-summary')).toContainText('This probe detects humorous vs not_humorous');
    await expect(page.getByRole('button', { name: 'Label 25,000 rows' })).toBeEnabled();
    const plans = seen.filter((c) => c.path === '/api/v1/label-runs/plan');
    expect(plans.length).toBeGreaterThan(0);
    await page.screenshot({ path: `e2e/captures/${mode}-novice-label-plan.png`, fullPage: true });

    // 7. Start: the step stays on Label and follows the run (finding 4), then compares the result
    //    with the data's own humor column (finding 6).
    await page.getByRole('button', { name: 'Label 25,000 rows' }).click();
    await expect(page.getByTestId('run-progress-rows')).toContainText('12,000 of 25,000 rows labeled (48.0%)', { timeout: 10_000 });
    await expect(page.getByTestId('label-step')).toBeVisible();
    await expect(page.getByTestId('run-progress-rows')).toContainText('25,000 of 25,000 rows labeled (100.0%)', { timeout: 15_000 });
    await expect(page.getByTestId('agreement-note')).toContainText('11.9%');
    await expect(page.getByTestId('agreement-figures')).toContainText('agree 58.1%');
    await page.screenshot({ path: `e2e/captures/${mode}-novice-label-run.png`, fullPage: true });
    expect(seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')).toHaveLength(1);
    await page.getByRole('button', { name: 'Continue to Assemble' }).click();
    await expect(page.getByTestId('label-step')).toHaveCount(0);
  });
}

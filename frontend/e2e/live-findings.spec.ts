import { expect, test } from '@playwright/test';

import { PROBE_PLAN, REPRODUCTION_WILL_RUN, probeRun } from '../src/test/fixtures005';
import { stubApi } from './fixtures';
import { api002 } from './fixtures002';
import { api005 } from './fixtures005';

// The 2026-10-08 live findings of the reproduction gate on screen, light and dark: one count (rows
// and distinct inputs, the input labelled both ways), the preflight's bar named, why the gate chose
// its target, a failed check that is not offered again (retry only with a reason), and a gate-failed
// run without Resume. Figures are production's (version edea5d8d, run lr_0631bc94, L11 probe).
const KEYS = {
  rows: 540, row_keys: 537, keys_with_copies: 1,
  conflicting: { count: 1, rows: 4, keys: [{ row_key: '1e98d06de79e7309c85c89db49bdcd00f1d6b3cc1927d7a6fc03c530bd703070', positive: 2, negative: 2 }] },
};
const LINKED = {
  ...REPRODUCTION_WILL_RUN, role: 'ood_eval', view_name: 'models-under-pressure mental_health_balanced', mistudio_auroc: 0.9417, mistudio_ci: [0.9205, 0.9598],
  source: 'linked_mistudio_evaluation', link_id: 'rpl_096ba754', link_check_level: 'content', snapshot_id: null, row_keys: KEYS,
  choice: {
    rule: 'snapshot, then link check level (content before counts_only), then role (id_test before ood_eval), then newest',
    why: 'a reproduction link whose rows were checked by content hash comes before a counts-only one',
    chosen: { source: 'linked_mistudio_evaluation', link_id: 'rpl_096ba754', snapshot_id: null, check_level: 'content', role: 'ood_eval', view_name: 'mup', version_id: 'v', split: 'test' },
    alternatives: [{ source: 'linked_mistudio_evaluation', link_id: 'rpl_b1ec881e', snapshot_id: null, check_level: 'counts_only', role: 'id_test', view_name: 'humor', version_id: 'v', split: 'test' }],
    alternatives_total: 1,
  },
  // Production's pm_f736aa73969d records the served render form (miStudio, captured 2026-10-08).
  scoring_form: {
    mistudio: {
      input_kinds: { json_messages: 540 }, scope: 'all', template_hash: 'e10ca381', max_length: 4096,
      render_form: { generation_prompt: true, add_special_tokens: false }, render_form_recorded: true, render_form_source: 'probe', render_served: true, mistudio_render_served: true,
      described_as: "inputs read as {'json_messages': 540}, rendered with the model's chat template WITH the generation prompt after a user turn and one BOS from the template (add_special_tokens false): the form miLLM serves",
    },
    millm: { input_form: 'messages', described_as: "each value sent to miLLM's POST /api/probes/score as the chat it holds", last_roles: { user: 540, assistant: 0, other: 0 } },
    agreement: 'equal_by_render_rule',
    token_ids_compared: false,
    reason: "miStudio recorded the served render form and miLLM renders by the same rule, so each row is the same token sequence by construction. Scored over scope 'all'. No token ids of these rows are available from miStudio, so the two are not compared token by token: this is not verified by token ids.",
  },
};
const FAILED = { ...LINKED, state: 'failed', failed_run_id: 'lr_0631bc949f58ab627bf0df95', millm_auroc: 0.961036, retry: 'Only a pass is cached, so the same check would fail the same way. Change something first, or start the run with reproduction_retry_reason.' };
const plan = (reproduction: unknown) => ({
  ...PROBE_PLAN,
  rows_total: 537, rows_to_score: 537,
  row_coverage: { rows: 540, row_keys: 537, keys_with_copies: 1, rows_in_copied_keys: 4, copies_disagree: { code: 'duplicate_metadata_disagrees', message: "Copies of 1 row key(s) disagree on ['ids', 'labels', 'status']." } },
  probe: {
    ...PROBE_PLAN.probe!, window_bar: { threshold: 25.289772033691406, provisional: false },
    preflight: {
      ...PROBE_PLAN.probe!.preflight, score: 9.900814056396484, threshold: 20.42040252685547, verdict: false,
      bar: { kind: 'length_band', window: 'all', window_threshold: 25.289772033691406, window_provisional: false, threshold: 20.42040252685547, n_tokens: 162, band: { min_tokens: 0, max_tokens: 203 }, label: "length band 0–203 tokens: 20.42; window 'all' bar: 25.29" },
    },
  },
  reproduction,
});

const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`label step: a failed check, one count, a named bar and the target choice (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    const a2 = api002([]);
    const a5 = api005(seen);
    await stubApi(page, {
      extra: (p, m, u, b) => {
        if (p === '/api/v1/label-runs/plan') {
          const request = JSON.parse(u.searchParams.get('request') ?? '{}') as { role?: string; reproduction_retry_reason?: string };
          if (request.role === 'probe') {
            return plan(request.reproduction_retry_reason
              ? { ...LINKED, retry_of: { run_id: FAILED.failed_run_id, millm_auroc: 0.961036, reason: request.reproduction_retry_reason } }
              : FAILED);
          }
        }
        return a5(p, m, u, b) ?? a2(p, m, u, b);
      },
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/new-dataset');
    await page.getByTestId('step-rail').getByText('label', { exact: true }).click();
    const step = page.getByTestId('label-step');
    await step.getByLabel('Version to label').selectOption({ index: 1 });
    await step.getByLabel('Label with').selectOption('probe');
    await step.getByLabel('Probe', { exact: true }).selectOption('pr_5ac12236c0dd');
    await step.getByLabel('The column holds').selectOption('messages');
    await expect(step.getByTestId('reproduction-line')).toHaveAttribute('data-state', 'failed');
    await expect(step.getByTestId('plan-line')).toContainText('540 rows, 537 distinct inputs');
    await expect(step.getByTestId('reproduction-conflicts')).toContainText('2 positive, 2 negative');
    await expect(step.getByTestId('probe-preflight')).toContainText("length band 0–203 tokens: 20.42; window 'all' bar: 25.29");
    await expect(step.getByTestId('probe-plan-line')).toContainText('window all bar 25.290');
    await expect(step.getByTestId('reproduction-choice')).toContainText('Not chosen: rpl_b1ec881e (counts only, id_test)');
    await expect(step.getByTestId('scoring-form')).toHaveAttribute('data-agreement', 'equal_by_render_rule');
    await expect(step.getByTestId('scoring-form')).toContainText('Equal by render rule, token ids not compared');
    await expect(step.getByTestId('scoring-form')).not.toContainText('no generation prompt');
    await expect(step.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-gate-failed-before.png`, fullPage: true });
    await step.getByLabel('Reason to retry the reproduction check').fill('miLLM redeployed with a fixed chat template');
    await step.getByRole('button', { name: 'Retry the check anyway' }).click();
    await expect(step.getByTestId('reproduction-line')).toHaveAttribute('data-state', 'will_run');
    await expect(step.getByTestId('reproduction-line')).toContainText('because: miLLM redeployed with a fixed chat template');
    await expect(step.getByRole('button', { name: /^Label .* rows$/ })).toBeEnabled();
    await page.screenshot({ path: `e2e/captures/${mode}-label-step-gate-retry.png`, fullPage: true });
  });

  test(`label runs: a gate-failed run offers no Resume (${mode})`, async ({ page }) => {
    const failed = probeRun({
      id: 'lr_1', state: 'failed', resumable: false,
      error: { code: 'REPRODUCTION_FAILED', message: "The reproduction check failed: scoring 540 rows of models-under-pressure mental_health_balanced through miLLM gave AUROC 0.9610, outside miStudio's reported 0.9417 [0.9205, 0.9598]." },
      not_resumable_reason: 'Label run lr_1 stopped at the reproduction check, and a resume would run the same check against the same rows. Change the link, window, input form or model revision, or start a new run with reproduction_retry_reason.',
      row_coverage: { rows: 540, row_keys: 537, keys_with_copies: 1, rows_in_copied_keys: 4, copies_disagree: null },
      rows_total: 537,
    });
    failed.endpoint_snapshot = { ...failed.endpoint_snapshot, reproduction: { ...FAILED, rows_scored: 540, rows_dropped: 0, failed_run_id: null, reason: 'AUROC 0.9610 is not inside the interval' } as never };
    const a5 = api005();
    await stubApi(page, {
      extra: (p, m, u, b) => (p === '/api/v1/label-runs' && m === 'GET' ? { items: [failed], total: 1, page: 1, limit: 50 } : a5(p, m, u, b)),
    });
    await page.addInitScript(theme, mode);
    await page.goto('/#/label-runs');
    // A stopped run recorded no rate: the card omits rows/s rather than showing a dash for it.
    await expect(page.getByTestId('label-run-card').first()).toContainText('of 537 rows');
    await expect(page.getByTestId('label-run-card').first()).not.toContainText('rows/s');
    await page.getByTestId('label-run-card').first().focus();
    await page.keyboard.press('Enter');
    const detail = page.getByTestId('label-run-detail');
    await expect(detail.getByTestId('not-resumable')).toContainText('a resume would run the same check');
    await expect(detail.getByRole('button', { name: 'Resume run' })).toHaveCount(0);
    await expect(detail.getByTestId('run-coverage')).toContainText('540 rows, 537 distinct inputs');
    await expect(detail.getByTestId('reproduction-conflicts')).toContainText('BOTH positive and negative');
    await page.screenshot({ path: `e2e/captures/${mode}-label-run-gate-failed-no-resume.png`, fullPage: true });
  });
}

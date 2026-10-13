// The 2026-10-08 live findings on screen: one count (rows and distinct inputs) with the conflicting
// key named, a failed reproduction check that is not offered again (no Resume; a retry needs a
// reason), the preflight's bar named (length band or the window's own), and why a gate target was
// chosen over the others. Figures are production's (L11 probe: band 0–203 at 20.42, window 25.29).
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLabelingStore } from '@/stores/labelingStore';
import { mockFetch } from '@/test/fetchMock';
import { PROBE_LIST, PROBE_PLAN, REPRODUCTION_WILL_RUN, RUBRIC, TEMPLATE, probeRun } from '@/test/fixtures005';
import type { KeySummary, LinkChecks, Plan, Reproduction } from '@/types/labeling';

import { LabelRunDetail } from './LabelRunDetail';
import { LabelStep, PLAN_DEBOUNCE_MS } from './LabelStep';
import { ChecksList } from './LinkEvaluation';
import { ProbePlanLine, ReproductionLine } from './ProbeLines';

const VERSIONS = { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'mup', target_type: 'untyped', number: 1, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 540, total_bytes: 1, warnings_count: 1 }], total: 1 };

const KEYS: KeySummary = {
  rows: 540, row_keys: 537, keys_with_copies: 1,
  conflicting: { count: 1, rows: 4, keys: [{ row_key: '1e98d06de79e7309c85c89db49bdcd00f1d6b3cc1927d7a6fc03c530bd703070', positive: 2, negative: 2 }] },
};

const BAND_PLAN: Plan = {
  ...PROBE_PLAN,
  rows_total: 537,
  rows_to_score: 537,
  row_coverage: {
    rows: 540, row_keys: 537, keys_with_copies: 1, rows_in_copied_keys: 4,
    copies_disagree: { code: 'duplicate_metadata_disagrees', message: "Copies of 1 row key(s) disagree on ['labels']." },
  },
  probe: {
    ...PROBE_PLAN.probe!,
    window_bar: { threshold: 25.289772033691406, provisional: false },
    preflight: {
      ...PROBE_PLAN.probe!.preflight,
      score: 9.900814056396484, threshold: 20.42040252685547, verdict: false,
      bar: {
        kind: 'length_band', window: 'all', window_threshold: 25.289772033691406, window_provisional: false,
        threshold: 20.42040252685547, n_tokens: 162, band: { min_tokens: 0, max_tokens: 203 },
        label: "length band 0–203 tokens: 20.42; window 'all' bar: 25.29",
      },
    },
  },
  reproduction: { ...REPRODUCTION_WILL_RUN, row_keys: KEYS },
};

const FAILED: Reproduction = {
  ...REPRODUCTION_WILL_RUN,
  state: 'failed',
  failed_run_id: 'lr_0631bc949f58ab627bf0df95',
  millm_auroc: 0.961036,
  retry: 'Only a pass is cached, so the same check would fail the same way.',
  row_keys: KEYS,
};

describe('2026-10-08 live findings on the Label step', () => {
  beforeEach(() => useLabelingStore.setState({ plan: null, sample: null, keepShare: null, started: null, approval: null, error: null, planError: null, calibration: {}, probes: null, probesError: null, probesUnconfigured: false, planRefusal: null, link: null, linkError: null, linkApproval: null }));
  afterEach(() => vi.unstubAllGlobals());

  async function chooseProbe() {
    await waitFor(() => expect(screen.getByRole('option', { name: /mup v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Version to label'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /high-stakes L16 mean/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
  }

  it('states rows and distinct inputs, and names the copies that disagree', async () => {
    mockFetch({
      'GET /api/v1/decision-templates': [TEMPLATE], 'GET /api/v1/rubrics': [RUBRIC], 'GET /api/v1/versions': VERSIONS,
      'GET /api/v1/labeling/probes': PROBE_LIST, 'GET /api/v1/label-runs/plan': BAND_PLAN,
    });
    render(<LabelStep />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('plan-line')).toBeInTheDocument(), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByTestId('plan-line')).toHaveTextContent('540 rows, 537 distinct inputs (1 input(s) appear in 4 rows; each is labelled once and the label applies to every copy)');
    expect(screen.getByTestId('plan-copies-disagree')).toHaveTextContent("disagree on ['labels']");
    expect(screen.getByTestId('reproduction-line')).toHaveTextContent('scores 540 rows, 537 distinct inputs, each scored once');
    expect(screen.getByTestId('reproduction-conflicts')).toHaveTextContent('1 input(s) appear in several rows labelled BOTH positive and negative (4 rows; e.g. 1e98d06de79e: 2 positive, 2 negative)');
    // Finding 4: both bars named, never a bare "bar" beside the window bar.
    expect(screen.getByTestId('probe-plan-line')).toHaveTextContent('window all bar 25.290 (probe score, not a probability)');
    expect(screen.getByTestId('probe-preflight')).toHaveTextContent("score 9.901 against length band 0–203 tokens: 20.42; window 'all' bar: 25.29 → silent");
  });

  it('a failed check reads failed, disables the start, and retries only with a reason', async () => {
    let calls: Array<{ method: string; path: string; body: unknown }> = [];
    const planned = () => {
      const last = calls.filter((c) => c.path.startsWith('/api/v1/label-runs/plan')).pop();
      const body = JSON.parse(new URLSearchParams(last!.path.split('?')[1]).get('request') ?? '{}');
      const reproduction = body.reproduction_retry_reason
        ? { ...REPRODUCTION_WILL_RUN, retry_of: { run_id: FAILED.failed_run_id, millm_auroc: 0.961036, reason: body.reproduction_retry_reason } }
        : FAILED;
      return { json: { ...BAND_PLAN, reproduction } };
    };
    ({ calls } = mockFetch({
      'GET /api/v1/decision-templates': [TEMPLATE], 'GET /api/v1/rubrics': [RUBRIC], 'GET /api/v1/versions': VERSIONS,
      'GET /api/v1/labeling/probes': PROBE_LIST, 'GET /api/v1/label-runs/plan': planned,
      'POST /api/v1/label-runs': () => ({ status: 201, json: probeRun({ state: 'queued' }) }),
    }));
    render(<LabelStep />);
    await chooseProbe();
    await waitFor(() => expect(screen.getByTestId('reproduction-line')).toHaveAttribute('data-state', 'failed'), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByTestId('reproduction-line')).toHaveTextContent('The same reproduction check already failed on run lr_0631bc949f58ab627bf0df95: miLLM AUROC 0.961');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
    const retry = screen.getByRole('button', { name: 'Retry the check anyway' });
    expect(retry).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Reason to retry the reproduction check'), { target: { value: 'miLLM redeployed' } });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByTestId('reproduction-line')).toHaveAttribute('data-state', 'will_run'), { timeout: PLAN_DEBOUNCE_MS + 2000 });
    expect(screen.getByTestId('reproduction-line')).toHaveTextContent('Retrying after run lr_0631bc949f58ab627bf0df95 failed, because: miLLM redeployed');
    const start = screen.getByRole('button', { name: /^Label .* rows$/ });
    expect(start).toBeEnabled();
    fireEvent.click(start);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/label-runs')).toBe(true));
    const posted = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/label-runs');
    expect(posted?.body).toMatchObject({ reproduction_retry_reason: 'miLLM redeployed' });
  });
});

describe('a gate-failed run', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('offers no Resume and says what to do instead', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_p/labels': { items: [], total: 0, page: 1, limit: 50 } });
    const reason = 'Label run lr_p stopped at the reproduction check, and a resume would run the same check against the same rows.';
    render(<LabelRunDetail run={probeRun({ state: 'failed', error: { code: 'REPRODUCTION_FAILED', message: 'failed' }, resumable: false, not_resumable_reason: reason })} />);
    expect(screen.queryByRole('button', { name: 'Resume run' })).not.toBeInTheDocument();
    expect(screen.getByTestId('not-resumable')).toHaveTextContent(reason);
  });

  it('a run the server calls resumable still offers Resume', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_p/labels': { items: [], total: 0, page: 1, limit: 50 } });
    render(<LabelRunDetail run={probeRun({ state: 'failed', error: { code: 'MODEL_CHANGED', message: 'x' }, resumable: true, not_resumable_reason: null })} />);
    expect(screen.getByRole('button', { name: 'Resume run' })).toBeInTheDocument();
  });
});

describe('why a gate target was chosen, and a link counted', () => {
  it('names the rule and every alternative with its check level', () => {
    const r: Reproduction = {
      ...REPRODUCTION_WILL_RUN,
      source: 'linked_mistudio_evaluation', link_id: 'rpl_096ba754', link_check_level: 'content',
      choice: {
        rule: 'snapshot, then link check level',
        why: 'a reproduction link whose rows were checked by content hash comes before a counts-only one',
        chosen: { source: 'linked_mistudio_evaluation', link_id: 'rpl_096ba754', snapshot_id: null, check_level: 'content', role: 'ood_eval', view_name: 'ood', version_id: 'v', split: 'test' },
        alternatives: [{ source: 'linked_mistudio_evaluation', link_id: 'rpl_b1ec881e', snapshot_id: null, check_level: 'counts_only', role: 'id_test', view_name: 'id', version_id: 'v', split: 'test' }],
        alternatives_total: 1,
      },
    };
    render(<ReproductionLine reproduction={r} />);
    expect(screen.getByTestId('reproduction-choice')).toHaveTextContent('Chosen because a reproduction link whose rows were checked by content hash comes before a counts-only one. Not chosen: rpl_b1ec881e (counts only, id_test).');
  });

  it('a link shows its rows, distinct inputs and conflicting key', () => {
    const checks: LinkChecks = {
      level: 'counts_only',
      row_count: { ran: true, ours: 540, mistudio: 540, passed: true },
      class_balance: { ran: true, ours: { positive: 270, negative: 270 }, mistudio: { positive: 270, negative: 270 }, passed: true },
      content: { ran: false, reason: 'not plain', order: 'file order' },
      row_keys: { ...KEYS, ran: true, passed: true },
    };
    render(<ChecksList checks={checks} />);
    expect(screen.getByTestId('link-keys')).toHaveTextContent('Counted: 540 rows, 537 distinct inputs, each scored once (1 appear in several rows).');
    expect(screen.getByTestId('link-conflicts')).toHaveTextContent('2 positive, 2 negative');
  });

  it('the plan line names a band bar without a window', () => {
    render(<ProbePlanLine probe={BAND_PLAN.probe!} />);
    expect(screen.getByTestId('probe-preflight')).toHaveTextContent('length band 0–203 tokens: 20.42');
  });
});

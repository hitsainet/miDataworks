// 009 FR-009.77 option (b): the Labeling screen's probe option when reproduction is unavailable.
// The refusal and link bodies are the backend's own, captured from
// backend/tests/integration/detector_sets/test_reproduction_links.py on 2026-10-07
// (src/test/captured009/), so the screen is tested against what the server actually sends.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLabelingStore } from '@/stores/labelingStore';
import { mockFetch } from '@/test/fetchMock';
import LINK from '@/test/captured009/reproduction_link.json';
import PLAN_LINKED from '@/test/captured009/reproduction_plan_linked.json';
import REFUSAL from '@/test/captured009/reproduction_refusal.json';
import SERVED from '@/test/captured009/reproduction_link_served.json';
import type { ScoringForm } from '@/types/labeling';
import { PROBE_LIST, RUBRIC, TEMPLATE } from '@/test/fixtures005';

import { LabelStep, PLAN_DEBOUNCE_MS } from './LabelStep';
import { ScoringFormLine } from './ProbeLines';

const VERSIONS = { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'humor-eval', target_type: 'untyped', number: 1, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 60, total_bytes: 1, warnings_count: 0 }], total: 1 };
const WAIT = { timeout: PLAN_DEBOUNCE_MS + 2000 };

function refused() {
  return { status: 422, json: REFUSAL };
}

describe('Link a miStudio evaluation', () => {
  beforeEach(() => useLabelingStore.setState({ plan: null, planError: null, planRefusal: null, probes: null, probesError: null, probesUnconfigured: false, link: null, linkError: null, linkApproval: null, calibration: {} }));
  afterEach(() => vi.unstubAllGlobals());

  async function toRefusal(routes: Record<string, unknown>) {
    const fetched = mockFetch({
      'GET /api/v1/decision-templates': [TEMPLATE],
      'GET /api/v1/rubrics': [RUBRIC],
      'GET /api/v1/versions': VERSIONS,
      'GET /api/v1/labeling/probes': PROBE_LIST,
      'GET /api/v1/calibration-status': { state: 'none' },
      ...routes,
    });
    render(<LabelStep />);
    await waitFor(() => expect(screen.getByRole('option', { name: /humor-eval v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Version to label'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /high-stakes L16 mean/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Probe'), { target: { value: 'pr_5ac12236c0dd' } });
    await waitFor(() => expect(screen.getByTestId('reproduction-refusal')).toBeInTheDocument(), WAIT);
    return fetched;
  }

  it('a refusal still shows the probe, its preflight against the bar, the state and both ways through', async () => {
    await toRefusal({ 'GET /api/v1/label-runs/plan': refused });
    const block = screen.getByTestId('reproduction-refusal');
    expect(within(block).getByTestId('probe-plan-line')).toHaveTextContent('Probe pr_humor');
    expect(within(block).getByTestId('probe-preflight')).toHaveTextContent('score 3.000 against bar 2.500 (which bar it is was not reported) → fires');
    expect(within(block).getByTestId('reproduction-unavailable')).toHaveAttribute('data-state', 'unavailable');
    const ways = within(block).getByTestId('reproduction-ways');
    expect(ways).toHaveTextContent('Send the detector set the probe was trained from');
    expect(ways).toHaveTextContent('link a version split holding the rows miStudio evaluated');
    expect(screen.getByRole('button', { name: /^Label .* rows$/ })).toBeDisabled();
  });

  it('links with exactly the chosen evaluation and split, shows every check, then plans against the link', async () => {
    let planned = 0;
    const { calls } = await toRefusal({
      'GET /api/v1/label-runs/plan': () => (planned++ === 0 ? refused() : { status: 200, json: PLAN_LINKED }),
      'POST /api/v1/reproduction-links': () => ({ status: 201, json: LINK }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Link a miStudio evaluation' }));
    const select = screen.getByLabelText('miStudio evaluation');
    expect(within(select).getByRole('option', { name: /Humor \(JEV-9B\) held-out test · in_distribution · AUROC 0\.9751 \[0\.9652, 0\.9837\] on 60 rows/ })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'pmd_fb14b0b206a5' } });
    expect(screen.getByLabelText('Split')).toHaveValue('test');
    expect(screen.getByLabelText('Input column')).toHaveValue('text');
    fireEvent.change(screen.getByLabelText('Version holding those rows'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and link' }));
    await waitFor(() => expect(screen.getByTestId('reproduction-target')).toHaveAttribute('data-source', 'linked_mistudio_evaluation'), WAIT);
    const posts = calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/reproduction-links');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ mistudio_probe_id: 'pm_c99519a98e08', probe_dataset_id: 'pmd_fb14b0b206a5', version_id: 'v1', split: 'test', input_column: 'text', label_column: 'label' });
    expect(screen.getByTestId('reproduction-target')).toHaveTextContent(`reproduction link ${LINK.id} (rows checked by content hash)`);
    // pm_c99519a98e08 records no render form: rendered without the generation prompt, which miLLM
    // adds to one user turn, so the server says the forms differ.
    expect(screen.getByTestId('scoring-form')).toHaveAttribute('data-agreement', 'differs');
    expect(screen.getByTestId('scoring-form')).toHaveTextContent('These DIFFER');
    expect(screen.getByTestId('scoring-form')).toHaveTextContent('not recorded - rendered without the generation prompt');
    expect(screen.queryByTestId('reproduction-refusal')).not.toBeInTheDocument();
  });

  it('a refused link shows the checks that differ and never claims a match', async () => {
    const checks = { ...LINK.checks, level: 'refused', row_count: { ran: true, ours: 2341, mistudio: 6600, passed: false }, content: { ran: false, reason: 'not compared', order: 'file order' } };
    await toRefusal({
      'GET /api/v1/label-runs/plan': refused,
      'POST /api/v1/reproduction-links': () => ({ status: 409, json: { error: { code: 'link_rows_differ', message: 'These are not the rows miStudio evaluated: 2,341 rows of the split map to a class, and miStudio evaluated 6,600.', details: { checks } } } }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Link a miStudio evaluation' }));
    fireEvent.change(screen.getByLabelText('miStudio evaluation'), { target: { value: 'pmd_c99c8595671d' } });
    fireEvent.change(screen.getByLabelText('Version holding those rows'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and link' }));
    const error = await screen.findByTestId('link-error');
    expect(error).toHaveTextContent('These are not the rows miStudio evaluated');
    expect(within(error).getByTestId('link-checks')).toHaveTextContent('2,341 rows map to a class here, miStudio evaluated 6,600 (differs)');
    expect(within(error).getByTestId('link-content')).toHaveTextContent('Content: not compared.');
    expect(screen.queryByTestId('link-made')).not.toBeInTheDocument();
  });

  it('a content hash that disagrees is reported as differing, never as a match', async () => {
    const checks = { ...LINK.checks, level: 'refused', content: { ...LINK.checks.content, ran: true, passed: false, ordered_match: false, unordered_match: false } };
    await toRefusal({
      'GET /api/v1/label-runs/plan': refused,
      'POST /api/v1/reproduction-links': () => ({ status: 409, json: { error: { code: 'link_rows_differ', message: 'These are not the rows miStudio evaluated: the input texts hash differently from the rows miStudio served.', details: { checks } } } }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Link a miStudio evaluation' }));
    fireEvent.change(screen.getByLabelText('miStudio evaluation'), { target: { value: 'pmd_fb14b0b206a5' } });
    fireEvent.change(screen.getByLabelText('Version holding those rows'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and link' }));
    const content = await screen.findByTestId('link-content');
    expect(content).toHaveTextContent('the input texts hash differently');
    expect(content).not.toHaveTextContent('hash the same');
  });

  it('a counts-only link says so, beside the gate', async () => {
    const countsOnly = { ...PLAN_LINKED, reproduction: { ...PLAN_LINKED.reproduction, link_check_level: 'counts_only' } };
    await toRefusal({
      'GET /api/v1/label-runs/plan': (() => { let n = 0; return () => (n++ === 0 ? refused() : { status: 200, json: countsOnly }); })(),
      'POST /api/v1/reproduction-links': () => ({ status: 201, json: { ...LINK, check_level: 'counts_only', checks: { ...LINK.checks, level: 'counts_only', content: { ran: false, reason: 'miStudio refused to serve the rows', order: 'file order' } } } }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Link a miStudio evaluation' }));
    fireEvent.change(screen.getByLabelText('miStudio evaluation'), { target: { value: 'pmd_fb14b0b206a5' } });
    fireEvent.change(screen.getByLabelText('Version holding those rows'), { target: { value: 'v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check and link' }));
    await waitFor(() => expect(screen.getByTestId('reproduction-target')).toHaveTextContent('counts only: row content was not compared'), WAIT);
  });

  it('when miStudio is not configured, the control says so instead of offering evaluations', async () => {
    const body = structuredClone(REFUSAL) as { error: { details: Record<string, unknown> } };
    body.error.details.link_candidates = { items: [], reason: 'miStudio is not configured (MISTUDIO_BASE_URL), so its recorded evaluations cannot be listed or linked.' };
    await toRefusal({ 'GET /api/v1/label-runs/plan': () => ({ status: 422, json: body }) });
    fireEvent.click(screen.getByRole('button', { name: 'Link a miStudio evaluation' }));
    expect(screen.getByTestId('link-candidates-reason')).toHaveTextContent('MISTUDIO_BASE_URL');
    expect(screen.queryByLabelText('miStudio evaluation')).not.toBeInTheDocument();
  });
});

describe('the render form a link read from miStudio (2026-10-08)', () => {
  it('a served probe reads equal by render rule and never claims token ids were compared', () => {
    render(<ScoringFormLine form={SERVED.scoring_form as ScoringForm} />);
    const line = screen.getByTestId('scoring-form');
    expect(line).toHaveAttribute('data-agreement', 'equal_by_render_rule');
    expect(line).toHaveTextContent('Equal by render rule, token ids not compared');
    expect(line).toHaveTextContent('WITH the generation prompt');
    expect(line).toHaveTextContent('not verified by token ids');
    expect(line).not.toHaveTextContent('no generation prompt');
    expect(screen.queryByTestId('scoring-form-note')).not.toBeInTheDocument();
  });

  it('an old link keeps its stored text and shows the note beside it', () => {
    const old = { ...(LINK.scoring_form as ScoringForm), agreement: 'not_verified' as const, note: 'This description was stored before miDataworks read the render form.' };
    render(<ScoringFormLine form={old} />);
    expect(screen.getByTestId('scoring-form')).toHaveTextContent('Not verified equal');
    expect(screen.getByTestId('scoring-form-note')).toHaveTextContent('Note: This description was stored before');
  });
});

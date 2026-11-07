// Feature 017 (FTASKS 12.1 – 12.6; FR-017.65): the blocks where the operator reads or decides, on
// the real panels and detail components with the real stores and API client against a fetch stub.
// Each block renders from a fixture response shaped like the backend's answer and reads "not
// measured" for a figure the response leaves out (FR-017.63); the behaviour filter's request is
// asserted (payload and call count). Removing a block's mount turns its test red.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApprovalCard } from '@/components/approvals/ApprovalCard';
import { GenerationRunDetail } from '@/components/generation/GenerationRunDetail';
import { LabelRunDetail } from '@/components/labeling/LabelRunDetail';
import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { VersionDetailPanel } from '@/components/panels/VersionDetailPanel';
import { getPanel } from '@/config/panels';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useVersionsStore } from '@/stores/versionsStore';
import { mockFetch } from '@/test/fetchMock';
import { META, VERSION } from '@/test/fixtures002';
import { run as labelRun } from '@/test/fixtures005';
import { SET } from '@/test/fixtures009';
import type { Approval } from '@/types/api';
import type { GenerationRun } from '@/types/generation';
import type { Version } from '@/types/versions';

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  useVersionsStore.setState({ selectedId: null, versions: {}, versionList: [], lineage: null, error: null });
  useDetectorSetsStore.setState({ sets: [], current: null, behaviourKey: '', error: null });
  useLabelRunsStore.setState({ list: [], byId: {}, selectedId: null, behaviourKey: '', error: null });
});

const PROOF = {
  split: 'ood_genre', passes: true, columns: ['genre'], identity_columns: [], held_out_values: { genre: ['report'] },
  group_columns: ['carrier_id'], rows: 214, generated_rows: 214, training_splits: ['train', 'test'],
  training_values: { genre: 2 }, intersection: [], intersection_count: 0, near_values: [], near_values_count: 0,
  null_rows: 0, outside_rows: [], outside_count: 0, shared_groups: { carrier_id: 0 }, rule: 'R-03.38 as amended, ENH-006 D10',
};

const BEHAVIOUR_VERSION: Version = {
  ...VERSION,
  total_rows: 1060,
  splits: [
    ...VERSION.splits,
    { name: 'ood_genre', held_out: true, rows: 214, bytes: 1, file_sha256: 'a'.repeat(64), logical_digest: 'b'.repeat(64), path: 'versions/v-2/ood.parquet', proof: PROOF },
  ],
  behaviour_definition: { key: 'some-behaviour', definition_version: 3, positive_meaning: 'the text shows it', verified: true, source: 'catalogue' },
  behaviour_census: {
    rows: 1060, planned_rows: 1200, cells_total: 12, cells_short: 1, counted: 'rows with carrier text',
    cells: [
      { genre: 'memo', placement: 'whole', framing: 'asks', length_band: 'short', planned: 100, actual: 60, shortfall: 40, short: true },
      { genre: 'forum', placement: 'whole', framing: 'asks', length_band: 'short', planned: 100, actual: 100, shortfall: 0, short: false },
    ],
    templated: { rows: 0, share: null, ceiling: 0.1, over_ceiling: false },
  },
};

function versionRoutes(version: Version) {
  return mockFetch({
    'GET /api/v1/datasets/meta': META,
    'GET /api/v1/versions': { items: [], total: 0, page: 1, limit: 200 },
    [`GET /api/v1/versions/${version.id}`]: version,
  });
}

describe('the version page (12.1)', () => {
  it('shows the held-out proof, the generated-rows caveat, the recorded definition and the census', async () => {
    versionRoutes(BEHAVIOUR_VERSION);
    useVersionsStore.setState({ selectedId: BEHAVIOUR_VERSION.id, versions: { [BEHAVIOUR_VERSION.id]: BEHAVIOUR_VERSION } });
    render(<VersionDetailPanel panel={getPanel('version')} />);
    const proof = await screen.findByTestId('held-out-proof');
    expect(proof).toHaveTextContent('Proof over 1,060 built rows');
    const split = within(proof).getByTestId('proof-ood_genre');
    expect(split).toHaveTextContent('ood_genre: passes');
    expect(split).toHaveTextContent('genre: report');
    expect(split).toHaveTextContent('carrier_id: 0');
    expect(within(proof).getByTestId('caveat-ood_genre')).toHaveTextContent('This held-out split holds generated rows');
    const definition = screen.getByTestId('recorded-definition');
    expect(definition).toHaveTextContent('some-behaviour');
    expect(definition).toHaveTextContent("miStudio's behaviour catalogue");
    const census = screen.getByTestId('census');
    expect(census).toHaveTextContent('1 of 12 cells short of plan');
    expect(census).toHaveTextContent('Templated rows: not measured');
    expect(census.querySelector('tr[data-short="yes"]')).toHaveTextContent('40 (short)');
    expect(census).toHaveTextContent('The 10 worst cells of 12');
  });

  it('a figure the record leaves out reads "not measured"; a version that is no behaviour build shows no block', async () => {
    const partial: Version = {
      ...BEHAVIOUR_VERSION,
      splits: BEHAVIOUR_VERSION.splits.map((s) => (s.proof ? { ...s, proof: { ...PROOF, shared_groups: null, generated_rows: undefined } } : s)),
      behaviour_definition: { key: 'some-behaviour', definition_version: null, positive_meaning: null, verified: false, source: 'supplied' },
    };
    versionRoutes(partial);
    useVersionsStore.setState({ selectedId: partial.id, versions: { [partial.id]: partial } });
    const { unmount } = render(<VersionDetailPanel panel={getPanel('version')} />);
    const split = await screen.findByTestId('proof-ood_genre');
    expect(split).toHaveTextContent('Groups shared with trainingnot measured');
    expect(split).toHaveTextContent('Generated rows in itnot measured');
    expect(screen.queryByTestId('caveat-ood_genre')).not.toBeInTheDocument();
    expect(screen.getByTestId('recorded-definition')).toHaveTextContent('nothing: supplied by hand, not verified');
    unmount();
    versionRoutes(VERSION);
    useVersionsStore.setState({ selectedId: VERSION.id, versions: { [VERSION.id]: VERSION } });
    render(<VersionDetailPanel panel={getPanel('version')} />);
    await screen.findByTestId('version-ids');
    expect(screen.queryByTestId('held-out-proof')).not.toBeInTheDocument();
    expect(screen.queryByTestId('census')).not.toBeInTheDocument();
  });
});

const CARRIER_RUN: GenerationRun = {
  id: 'gr_c', mode: 'carrier_slot', target_type: 'detector', state: 'completed', input_version_id: 'v-plan',
  held_out_splits: ['test'], prompt_column: 'text', seed_splits: ['train'], sample_size: 120, seed: 5,
  n_responses: 1, stages: ['respond'], generation_endpoint: {}, server_kind: 'millm',
  generator_identities: [{ model_id: 'gen-a', revision: 'abc123def456', set_hash: 'none' }], judge_identity: null,
  chosen_side: null, engine_path: 'native', pinned: true, revision_reported: true, model_revision: 'abc', failure_reason: null,
  error: null, warnings: [], counts: { 'respond:generated': 54, 'reason:slot_missing': 40, 'reason:slot_repeated': 26 },
  snapshots: [], started_by: 'agent:dataworks-mcp', started_by_origin: 'agent', created_at: '', completed_at: '',
  job_ids: ['job_1'], current_job_id: null, room: 'dataworks/generation-runs/gr_c', resumable: false,
  generator_model_id: 'gen-a', behaviour_key: 'some-behaviour',
};

describe('the generation page (12.2)', () => {
  it("a carrier run shows its slot drops, and throughput reads 'not measured' until measured", () => {
    useGenerationStore.setState({ records: {}, pairs: {}, live: {} });
    mockFetch({ 'GET /api/v1/generation-runs/gr_c/records': { items: [], total: 0, page: 1, limit: 50 } });
    render(<GenerationRunDetail run={CARRIER_RUN} />);
    const block = screen.getByTestId('carrier-run');
    expect(block).toHaveTextContent('Kept54');
    expect(block).toHaveTextContent('Dropped: no slot40');
    expect(block).toHaveTextContent('Dropped: slot twice26');
    expect(block).toHaveTextContent('Rows per run-hournot measured');
    expect(block).toHaveTextContent('Behaviour keysome-behaviour');
  });
});

describe('the label run page (12.3)', () => {
  it('shows the cost basis and the agreement by stratum, worst first, with partial coverage said', async () => {
    const run = labelRun({
      id: 'lr_s', state: 'completed', kind: 'classifier',
      cost_basis: { paid: true, protocol: 'typesafe_systemone', price_per_call_usd: 0.000015, rows_planned: 2000, questions: 1, estimated_usd: null },
    });
    mockFetch({
      'GET /api/v1/label-runs/lr_s/labels': { items: [], total: 0, page: 1, limit: 500 },
      'GET /api/v1/label-runs/lr_s/agreement': {
        label_run_id: 'lr_s', run_state: 'completed', applicable: true, reason: null, columns: [{ name: 'truth', values: ['false', 'true'] }],
        column: 'truth', values: ['false', 'true'], positive_value: 'true', needs_positive_value: false, rows_compared: 30,
        rows_not_compared: 0, counts: { both_positive: 10, run_only_positive: 2, column_only_positive: 3, both_negative: 15 },
        accuracy: 0.83, run_positive_share: 0.4, column_positive_share: 0.43, auroc: null, auroc_from: null, note: 'Close.',
        strata: {
          by: ['genre'], cells_total: 2, cells_not_measured: 1, next_page_token: null, coverage: { rows: 40, scored: 30 },
          cells: [
            { stratum: { genre: 'memo' }, rows: 30, scored: 30, agreement: 0.83, kappa: 0.61 },
            { stratum: { genre: 'forum' }, rows: 10, scored: 0, agreement: 'not measured', kappa: 'not measured' },
          ],
        },
      },
    });
    render(<LabelRunDetail run={run} />);
    const cost = screen.getByTestId('cost-basis');
    expect(cost).toHaveTextContent('Price per call, in US dollars$0.0000');
    expect(cost).toHaveTextContent('Rows planned2,000');
    expect(cost).toHaveTextContent('Estimated cost, in US dollarsnot measured');
    const strata = await screen.findByTestId('agreement-by-stratum');
    expect(strata).toHaveTextContent('30 of 40 rows scored (partial');
    const rows = within(strata).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('memo');
    expect(rows[1]).toHaveTextContent('83.0%');
    expect(rows[2]).toHaveTextContent('forum');
    expect(rows[2]).toHaveTextContent('not measured');
  });
});

const PAID: Approval = {
  id: 'apr_p', action: 'paid_classifier_run', target: 'src.api.v1.endpoints.label_runs.start_label_run',
  summary: 'Paid classifier run: 36 rows', request_digest: 'd'.repeat(64), requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: '2099-01-01T00:00:00Z', decided_by: null, decided_at: null, reason: null, result_kind: null, result_id: null,
  error: null, created_at: '2026-10-28T00:00:00Z',
  payload: {
    body: { role: 'classifier' },
    paid: { rows: 36, cap_rows: 2500, estimated_usd: 0.0288, price_per_call_usd: 0.0008, spent_usd: 0, ceiling_usd: 5,
            text_leaves: "The selected rows' text is sent to https://classifier.example:443." },
  },
} as unknown as Approval;

describe('the approvals card (12.4)', () => {
  it('a paid classifier run shows rows, cap, cost and where the text goes', () => {
    render(<ApprovalCard request={PAID} now={Date.parse("2026-10-28T00:00:00Z")} />);
    const card = screen.getByTestId('paid-run-card');
    expect(card).toHaveTextContent('36 rows (cap 2,500 per run) at $0.0008 a call: about $0.0288.');
    expect(card).toHaveTextContent('Spent so far $0.0000 of a ceiling of $5.0000.');
    expect(within(card).getByRole('note')).toHaveTextContent("The selected rows' text is sent to https://classifier.example:443.");
  });
});

describe('the behaviour filter on the lists (12.5)', () => {
  it('the detector sets list sends behaviour_key once per valid key, and nothing for a bad one', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/detector-sets': { items: [SET], total: 1 } });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    const input = await screen.findByTestId('sets-behaviour-filter');
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/v1/detector-sets')).length).toBe(1));
    fireEvent.change(input, { target: { value: 'Bad Key' } });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Lower case letters, digits and hyphens')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'some-behaviour' } });
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/v1/detector-sets')).length).toBe(2));
    const lists = calls.filter((c) => c.path.startsWith('/api/v1/detector-sets'));
    expect(lists.map((c) => c.path)).toEqual([
      '/api/v1/detector-sets?limit=200',
      '/api/v1/detector-sets?limit=200&behaviour_key=some-behaviour',
    ]);
  });

  it('the label runs list sends behaviour_key with the archive switch', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/label-runs': { items: [], total: 0, page: 1, limit: 50 } });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    const input = await screen.findByTestId('runs-behaviour-filter');
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/v1/label-runs')).length).toBe(1));
    fireEvent.change(input, { target: { value: 'some-behaviour' } });
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith('/api/v1/label-runs')).length).toBe(2));
    expect(calls.filter((c) => c.path.startsWith('/api/v1/label-runs')).map((c) => c.path)).toEqual([
      '/api/v1/label-runs',
      '/api/v1/label-runs?behaviour_key=some-behaviour',
    ]);
  });
});

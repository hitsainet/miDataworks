// Feature 004's panels (FTASKS 13.2–13.9) through the real store and API client over a fetch stub.
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { discoveredSlots } from '@/components/versions/versionSlots';
import { useCurationStore } from '@/stores/curationStore';
import { mockFetch } from '@/test/fetchMock';
import type { AuditView, ShortcutColumn } from '@/types/curation';
import type { Version } from '@/types/versions';

import { CellBalancerDialog } from './CellBalancerDialog';
import { ProfileHistograms } from './ProfileHistograms';
import { ShortcutLevelControl } from './ShortcutLevelControl';
import { CrossTabSlot } from './versionDetailSlot.crossTab';
import { LeakageSlot } from './versionDetailSlot.leakage';
import { ShortcutAuditSlot } from './versionDetailSlot.shortcutAudit';

const VERSION = {
  id: 'v1',
  dataset_id: 'ds1',
  splits: [
    { name: 'train', held_out: false, rows: 9824 },
    { name: 'test', held_out: true, rows: 1090 },
  ],
} as unknown as Version;

const col = (column: string, figure: number, extra: Partial<ShortcutColumn> = {}): ShortcutColumn => ({
  column, kind: 'metadata', n_rows: 10914, n_values: 2, classes: ['humorous', 'not_humorous'], chance: 0.5, figure,
  control_mean: 0.5, control_runs: 5, folds: 5, valid: true, invalid_reason: null,
  per_value: {
    values: [
      { value: 'joke', counts_by_label: { humorous: 4841, not_humorous: 642 } },
      { value: 'headline', counts_by_label: { humorous: 616, not_humorous: 4815 } },
    ],
    other: null,
    other_values: 0,
  },
  bins: null,
  ...extra,
});

const VIEW: AuditView = {
  audit: {
    label_column: 'label', label_source: 'labeler', classes: ['humorous', 'not_humorous'],
    class_counts: { humorous: 5457, not_humorous: 5457 }, n_rows: 10914, unlabelled_rows: 0, chance: 0.5,
    columns: [col('format', 0.885), col('id', 0.5, { n_values: 10914 }), col('odd', 0.6, { valid: false, control_mean: 0.56, invalid_reason: 'control high' })],
    excluded_columns: [{ column: 'label_probability', reason: 'label_derived', source_operator: 'threshold_labeler@1.0.0' }],
    excluded_by_band: { format: { joke: 5958, headline: 5166 } },
    sample_seed: 7,
  },
  warnings: [
    {
      version_id: 'v1', column: 'format', figure: 0.885, chance: 0.5, control_mean: 0.5, level: 10, margin_pp: 10,
      level_source: 'code_default', level_set_by: null, n_rows: 10914, sample: false,
      message: 'Format predicts the label 88.5% of the time (held-out balanced accuracy; chance is 50%; on 10,914 rows).',
    },
  ],
  invalid: ['odd'],
  level: { margin_pp: 10, source: 'code_default', set_by: null, set_at: null, reason: null },
};

const LEVEL = { effective_margin_pp: 10, source: 'code_default', level: VIEW.level, history: [] };

beforeEach(() => {
  useCurationStore.setState({ auditsByVersion: {}, profilesByVersion: {}, leakageByVersion: {}, levelsByDataset: {}, cellSamples: {}, balancer: null, running: {}, errors: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('discovery', () => {
  it('feature 002 discovers the four slots and three guided steps', () => {
    expect(discoveredSlots().map((s) => s.id)).toEqual(expect.arrayContaining(['shortcut-audit', 'cross-tab', 'profile', 'leakage']));
    expect(discoveredStepPanels().map((p) => p.step)).toEqual(expect.arrayContaining(['profile', 'curate', 'assemble']));
  });
});

describe('the shortcut audit slot', () => {
  it('shows the tile, the warning, status pills with text and the excluded columns', async () => {
    mockFetch({ 'GET /api/v1/versions/v1/shortcut-audit': VIEW, 'GET /api/v1/datasets/ds1/shortcut-level': LEVEL });
    render(<ShortcutAuditSlot version={VERSION} />);
    expect(await screen.findByTestId('shortcut-tile')).toHaveTextContent('Format predicts label88.5%');
    expect(screen.getByRole('alert')).toHaveTextContent('Format predicts the label 88.5% of the time');
    expect(screen.getByText('Warns')).toBeInTheDocument();
    expect(screen.getByText('Clear')).toBeInTheDocument();
    expect(screen.getByText('Control failed')).toBeInTheDocument();
    expect(screen.getByText(/written by threshold_labeler@1.0.0/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Build format-balanced version' })).toBeInTheDocument();
  });

  it('offers to run when no audit exists, and shows the refusal', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/versions/v1/shortcut-audit': () => ({ status: 404, json: { error: { code: 'audit_not_run', message: 'not run', details: {} } } }),
      'POST /api/v1/versions/v1/shortcut-audit': () => ({ status: 422, json: { error: { code: 'no_label_column', message: 'This version has no label column.', details: {} } } }),
    });
    render(<ShortcutAuditSlot version={VERSION} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Run the shortcut audit' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no label column');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('shows a loading state first', () => {
    mockFetch({});
    render(<ShortcutAuditSlot version={VERSION} />);
    expect(screen.getByText('Loading the shortcut audit…')).toBeInTheDocument();
  });
});

describe('the cross-tab', () => {
  it('renders counts with the excluded column and opens seeded samples', async () => {
    useCurationStore.setState({ auditsByVersion: { v1: VIEW } });
    const { calls } = mockFetch({
      'GET /api/v1/versions/v1/shortcut-audit/cells': { rows: [{ row_key: 'k', occurrence: 0, label: 'humorous', excerpt: { text: 'a pun' } }], seed: 7, total: 30, cell_rows: 4841 },
    });
    render(<CrossTabSlot version={VERSION} />);
    expect(screen.getByText('Excluded by the labeler')).toBeInTheDocument();
    expect(screen.getByText('5,958')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Samples for joke and humorous' }));
    expect(await screen.findByText('a pun')).toBeInTheDocument();
    expect(calls[0].path).toContain('column=format&value=joke&label=humorous');
  });

  it('says when the audit has not run', () => {
    render(<CrossTabSlot version={VERSION} />);
    expect(screen.getByText(/appears once the shortcut audit has run/)).toBeInTheDocument();
  });
});

describe('the cell balancer dialog', () => {
  it('previews cells, flags a value and re-previews with it excluded', async () => {
    const report = {
      column: 'format', label_column: 'label', cap: 613, rows_in: 10914, rows_kept: 2452, rows_dropped: 8462, excluded_values: [],
      cells: { 'joke|humorous': { value: 'joke', label: 'humorous', before: 4841, after: 613 } },
      extreme_values: [
        { column: 'format', value: 'news', dominant_label: 'not_humorous', dominant_rows: 1387, rows: 1390 },
        { column: 'source_label', value: 'news', dominant_label: 'not_humorous', dominant_rows: 1387, rows: 1390 },
      ],
      reaudit: { ...VIEW.audit, columns: [col('format', 0.5)] },
    };
    const { calls } = mockFetch({
      'POST /api/v1/operators/cell_balancer/1.0.0/preview': { status: 'done', result: { report, counts: { in: 10914, kept: 2452, dropped: 8462 } } },
    });
    render(<CellBalancerDialog versionId="v1" datasetId="ds1" column="format" audit={VIEW.audit} onClose={() => undefined} />);
    expect(await screen.findByText(/Cap 613 per cell/)).toBeInTheDocument();
    expect(screen.getByText(/Add a metadata value filter on source_label/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Exclude news' }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect((calls[1].body as { params: { exclude_values: string[]; exclude_columns: string[] } }).params).toMatchObject({
      exclude_values: ['news'],
      exclude_columns: ['label_probability'],
    });
  });

  it('builds a new version from this one with the balancer as its recipe', async () => {
    useCurationStore.setState({
      balancer: { report: { column: 'format', label_column: 'label', cap: 1, cells: {}, rows_in: 1, rows_kept: 1, rows_dropped: 0, excluded_values: [], extreme_values: [], reaudit: { refused: { code: 'x', message: 'x' } } }, counts: { in: 1, kept: 1, dropped: 0 } },
    });
    const { calls } = mockFetch({
      'POST /api/v1/operators/cell_balancer/1.0.0/preview': { status: 'done', result: { report: useCurationStore.getState().balancer!.report, counts: { in: 1, kept: 1, dropped: 0 } } },
      'POST /api/v1/recipes': () => ({ status: 201, json: { id: 'r1', head_revision_id: 'rev1' } }),
      'POST /api/v1/versions': () => ({ status: 202, json: { job_id: 'job_b', existing_job: false, seed: 1, request_digest: 'd' } }),
    });
    render(<CellBalancerDialog versionId="v1" datasetId="ds1" column="format" audit={VIEW.audit} onClose={() => undefined} />);
    await screen.findByText(/Cap 1 per cell/);
    await userEvent.click(screen.getByRole('button', { name: 'Build format-balanced version' }));
    expect(await screen.findByText(/Build started \(job_b\)/)).toBeInTheDocument();
    const build = calls.find((c) => c.path === '/api/v1/versions');
    expect(build?.body).toMatchObject({ dataset_id: 'ds1', inputs: [{ kind: 'version', version_id: 'v1' }], recipe_revision_id: 'rev1' });
  });
});

describe('the level control', () => {
  it('needs a reason and shows the operator-only message on 403', async () => {
    mockFetch({
      'GET /api/v1/datasets/ds1/shortcut-level': LEVEL,
      'PUT /api/v1/datasets/ds1/shortcut-level': () => ({ status: 403, json: { error: { code: 'agent_forbidden', message: 'x', details: {} } } }),
    });
    render(<ShortcutLevelControl datasetId="ds1" />);
    expect(await screen.findByText(/the default \(10 points, P-19\)/)).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save the level' });
    await userEvent.type(screen.getByLabelText('New level in points'), '25');
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Reason for the change'), 'format is the target');
    await userEvent.click(save);
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the operator can change this level.');
  });

  it('the global control reads and writes the settings route', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/settings/shortcut-level': { margin_pp: 10, source: 'code_default', level: VIEW.level, history: [] },
      'PUT /api/v1/settings/shortcut-level': () => ({ status: 201, json: { margin_pp: 12, source: 'set', level: { ...VIEW.level, margin_pp: 12, source: 'global' }, history: [{ action: 'set', margin_pp: 12, reason: 'r', set_by: 'Ada', origin: 'operator', created_at: '2026-10-07T00:00:00Z' }] } }),
    });
    render(<ShortcutLevelControl datasetId={null} />);
    await screen.findByText(/the default \(10 points, P-19\)/);
    await userEvent.type(screen.getByLabelText('New level in points'), '12');
    await userEvent.type(screen.getByLabelText('Reason for the change'), 'r');
    await userEvent.click(screen.getByRole('button', { name: 'Save the level' }));
    expect(await screen.findByText(/Set to 12 points by Ada/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ margin_pp: 12, reason: 'r' });
  });
});

describe('the profile', () => {
  it('shows not-computed figures with their reason, never a zero', () => {
    render(
      <ProfileHistograms
        profile={{
          n_rows: 300,
          sample: true,
          figures: {
            language: { status: 'not_computed', reason: 'No language-identification operator is allowlisted.', action: 'Allowlist one.' },
            exact_duplicates: { status: 'computed', n_rows: 300, sample: true, groups: 2, rows: 3 },
          },
        }}
      />,
    );
    expect(screen.getByText(/a sample: every figure is an estimate/)).toBeInTheDocument();
    expect(screen.getByText('Language: not computed')).toBeInTheDocument();
    expect(screen.getByText(/2 groups, 3 extra rows, on 300 rows/)).toBeInTheDocument();
  });
});

describe('the leakage slot', () => {
  it('renders a report with no pairs loaded without re-rendering forever (defect D4)', async () => {
    mockFetch({});
    useCurationStore.setState({
      leakageByVersion: {
        v1: { sides: ['test', 'train'], exact_pairs: {}, near_pairs: {}, group_pairs: { 'test|train': 1 }, basis: 'lexical', threshold: 0.8, group_column: 'pair_id', n_rows: 10914, pairs_total: 1 },
      },
    });
    render(<LeakageSlot version={VERSION} />);
    expect(await screen.findByText(/1 pair\(s\) cross between splits/)).toBeInTheDocument();
    expect(screen.getByText(/group: test ↔ train — 1/)).toBeInTheDocument();
  });
});

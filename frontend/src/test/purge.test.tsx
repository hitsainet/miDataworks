// Purge (operator decision 2026-10-20): the real panels, stores and API client against a fetch
// stub. Selecting, select all / deselect all (with the indeterminate state), the live count, the
// blocked item that cannot be selected, the confirmation's per-item plan and the exact batch sent.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApprovalHistoryCard } from '@/components/approvals/ApprovalHistoryCard';
import { describeCounts, describeResult, describeResults } from '@/components/common/PurgeControls';
import { DatasetsPanel } from '@/components/panels/DatasetsPanel';
import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { getPanel } from '@/config/panels';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { usePurgeStore } from '@/stores/purgeStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { mockFetch } from '@/test/fetchMock';
import { META } from '@/test/fixtures001';
import { DATASET } from '@/test/fixtures002';
import { run } from '@/test/fixtures005';
import type { Approval } from '@/types/api';
import type { PurgeItem, PurgeOutcome } from '@/types/purge';

type Call = { method: string; path: string; body: unknown };
const ARCHIVED = {
  archived_at: '2026-10-20T14:05:00Z',
  archived_by: 'Sean',
  archive_reason: 'e2e',
};
const SHOWN = { archived_at: null, archived_by: null, archive_reason: null };
const page = (items: unknown[]) => ({ items, total: items.length, page: 1, limit: 200 });
const purges = (calls: Call[]) =>
  calls
    .filter((c) => c.method === 'POST' && c.path === '/api/v1/purge')
    .map((c) => c.body as { items: PurgeItem[]; reason?: string });

const outcome = (item: PurgeItem, over: Partial<PurgeOutcome> = {}): PurgeOutcome => ({
  kind: item.kind,
  id: item.id,
  name: item.id,
  status: 'purgeable',
  code: null,
  message: null,
  blockers: [],
  counts: { versions: 2, labels: 30 },
  bytes: 2048,
  paths: 2,
  paths_left: [],
  tombstone_id: null,
  ...over,
});

/** The backend's plan and purge, with `blocked` ids refused purge_blocked. */
function purgeRoutes(blocked: Record<string, string> = {}) {
  const answer = (items: PurgeItem[], done: boolean) => {
    const out = items.map((i) =>
      blocked[i.id]
        ? outcome(i, {
            status: 'blocked',
            code: 'purge_blocked',
            message: blocked[i.id],
            counts: {},
            bytes: 0,
          })
        : outcome(i, done ? { status: 'purged', tombstone_id: `prg_${i.id}` } : {}),
    );
    return {
      items: out,
      purged: out.filter((o) => o.status === 'purged').length,
      blocked: out.filter((o) => o.status === 'blocked').length,
      bytes: 0,
    };
  };
  return {
    'POST /api/v1/purge/plan': (body: unknown) => ({
      json: answer((body as { items: PurgeItem[] }).items, false),
    }),
    'POST /api/v1/purge': (body: unknown) => ({
      json: answer((body as { items: PurgeItem[] }).items, true),
    }),
  };
}

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => usePurgeStore.setState({ plans: {}, error: null }));

describe('Datasets screen: delete permanently', () => {
  beforeEach(() => useSourcesStore.setState({ meta: META, sources: [] }));

  const datasets = [
    { ...DATASET, id: 'd-a', name: 'alpha', ...ARCHIVED },
    { ...DATASET, id: 'd-b', name: 'bravo', ...ARCHIVED },
    { ...DATASET, id: 'd-c', name: 'charlie', ...ARCHIVED },
    { ...DATASET, id: 'd-blocked', name: 'ops-8-11', ...ARCHIVED },
    { ...DATASET, id: 'd-shown', name: 'kept', ...SHOWN },
  ];

  async function open(
    blocked: Record<string, string> = {
      'd-blocked': 'publish record pub_1 still uses this version.',
    },
  ) {
    let gone = new Set<string>();
    const routes = purgeRoutes(blocked);
    const mock = mockFetch({
      'GET /api/v1/datasets': () => ({ json: page(datasets.filter((d) => !gone.has(d.id))) }),
      'GET /api/v1/sources/meta': META,
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      'POST /api/v1/purge/plan': routes['POST /api/v1/purge/plan'],
      'POST /api/v1/purge': (body: unknown) => {
        const out = routes['POST /api/v1/purge'](body);
        gone = new Set([
          ...gone,
          ...out.json.items.filter((o) => o.status === 'purged').map((o) => o.id),
        ]);
        return out;
      },
    });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    await screen.findAllByTestId('dataset-card');
    expect(screen.queryByTestId('purge-bar')).toBeNull(); // nothing to purge until archived are shown
    fireEvent.click(screen.getByTestId('show-archived-datasets'));
    await screen.findByTestId('purge-bar');
    await waitFor(() => expect(Object.keys(usePurgeStore.getState().plans)).toHaveLength(4));
    expect(screen.queryAllByTestId('purge-blocker')).toHaveLength(Object.keys(blocked).length);
    return mock;
  }

  const box = (name: string) =>
    screen.getByRole('checkbox', { name: `Select dataset ${name} for permanent delete` });

  it('offers a checkbox on archived datasets only; a blocked one shows why and cannot be selected', async () => {
    await open();
    expect(screen.getAllByTestId('purge-checkbox')).toHaveLength(4); // not on the shown dataset
    const blocked = screen.getByRole('checkbox', {
      name: 'dataset ops-8-11 cannot be deleted permanently',
    });
    expect(blocked).toBeDisabled();
    expect(screen.getByTestId('purge-blocker')).toHaveTextContent(
      'publish record pub_1 still uses this version.',
    );
    expect(screen.getByTestId('purge-count')).toHaveTextContent('0 selected · 1 cannot be deleted');
    expect(screen.getByTestId('purge-open')).toBeDisabled();
  });

  it('select all, deselect one (indeterminate), deselect all, individual select', async () => {
    await open();
    const all = screen.getByTestId('purge-select-all') as HTMLInputElement;
    fireEvent.click(all);
    expect(screen.getByTestId('purge-count')).toHaveTextContent('3 selected');
    expect(all.checked).toBe(true);
    expect(all.indeterminate).toBe(false);
    expect(screen.getByText(/Deselect all archived datasets/)).toBeInTheDocument();
    fireEvent.click(box('bravo'));
    expect(screen.getByTestId('purge-count')).toHaveTextContent('2 selected');
    expect(all.checked).toBe(false);
    expect(all.indeterminate).toBe(true);
    fireEvent.click(all); // partly selected: selects all
    expect(screen.getByTestId('purge-count')).toHaveTextContent('3 selected');
    fireEvent.click(all); // all selected: deselects all
    expect(screen.getByTestId('purge-count')).toHaveTextContent('0 selected');
    expect(all.indeterminate).toBe(false);
    fireEvent.click(box('charlie'));
    expect(screen.getByTestId('purge-count')).toHaveTextContent('1 selected');
    expect(box('charlie')).toBeChecked();
    expect(box('alpha')).not.toBeChecked();
  });

  it('confirms with the plan of each selected item and sends exactly the selection', async () => {
    const { calls } = await open();
    fireEvent.click(screen.getByTestId('purge-select-all'));
    fireEvent.click(box('alpha'));
    fireEvent.click(screen.getByTestId('purge-open'));
    const dialog = await screen.findByTestId('purge-plan');
    const items = within(dialog).getAllByTestId('purge-plan-item');
    expect(items.map((li) => li.textContent)).toEqual([
      'd-b: removes 2 versions, 30 labels; 2.0 KB on disk',
      'd-c: removes 2 versions, 30 labels; 2.0 KB on disk',
    ]);
    expect(within(dialog).getByTestId('purge-total')).toHaveTextContent(
      'Frees 4.0 KB across 2 datasets.',
    );
    fireEvent.change(within(dialog).getByTestId('purge-reason'), {
      target: { value: 'e2e cleanup' },
    });
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(calls)).toEqual([
      {
        items: [
          { kind: 'dataset', id: 'd-b' },
          { kind: 'dataset', id: 'd-c' },
        ],
        reason: 'e2e cleanup',
      },
    ]);
    expect(screen.getAllByTestId('purge-result').map((r) => r.dataset.status)).toEqual([
      'purged',
      'purged',
    ]);
    // The list is reloaded: the purged datasets are gone, the others stay.
    await waitFor(() => expect(screen.getAllByTestId('dataset-card')).toHaveLength(3));
    expect(screen.getByTestId('purge-count')).toHaveTextContent('0 selected');
  });

  it('reports an item an earlier item of the batch took with it as deleted, with that item', async () => {
    const { calls } = await open({});
    fireEvent.click(box('bravo'));
    fireEvent.click(box('charlie'));
    vi.unstubAllGlobals();
    const routes = purgeRoutes();
    const second = mockFetch({
      'GET /api/v1/datasets': page(datasets),
      'POST /api/v1/purge/plan': routes['POST /api/v1/purge/plan'],
      // bravo's purge removed charlie with it: charlie comes back purged, removed_with bravo.
      'POST /api/v1/purge': () => {
        const items = [
          outcome(
            { kind: 'dataset', id: 'd-b' },
            {
              name: 'bravo',
              status: 'purged',
              tombstone_id: 'prg_b',
            },
          ),
          outcome(
            { kind: 'dataset', id: 'd-c' },
            {
              name: null,
              status: 'purged',
              code: 'removed_with',
              counts: {},
              bytes: 0,
              paths: 0,
              tombstone_id: 'prg_b',
              removed_with: { kind: 'dataset', id: 'd-b', name: 'bravo' },
            },
          ),
        ];
        return { json: { items, purged: 2, blocked: 0, bytes: 2048 } };
      },
    });
    fireEvent.click(screen.getByTestId('purge-open'));
    await screen.findByTestId('purge-plan');
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(second.calls)).toHaveLength(1);
    expect(purges(calls)).toEqual([]);
    expect(screen.getByTestId('purge-summary')).toHaveTextContent(
      'Deleted 2 of 2 (1 with the item they belonged to); freed 2.0 KB.',
    );
    const results = screen.getAllByTestId('purge-result');
    expect(results.map((r) => r.dataset.status)).toEqual(['purged', 'purged']);
    expect(results[1]).toHaveTextContent(
      'charlie: deleted with dataset bravo, whose record counts it',
    );
    expect(results[1]).not.toHaveTextContent('not deleted');
  });

  it('skips an item the fresh plan now refuses, and says so', async () => {
    const { calls } = await open({});
    fireEvent.click(box('alpha'));
    fireEvent.click(box('bravo'));
    // Between the list's plan and the confirmation, a job started on bravo.
    usePurgeStore.setState({ plans: {} });
    const routes = purgeRoutes({ 'd-b': 'Job job_9 is working on it.' });
    vi.unstubAllGlobals();
    const second = mockFetch({
      'GET /api/v1/datasets': page(datasets),
      'POST /api/v1/purge/plan': routes['POST /api/v1/purge/plan'],
      'POST /api/v1/purge': routes['POST /api/v1/purge'],
    });
    fireEvent.click(screen.getByTestId('purge-open'));
    const dialog = await screen.findByTestId('purge-plan');
    expect(within(dialog).getByTestId('purge-skipped')).toHaveTextContent(
      '1 of 2 cannot be deleted and will be skipped.',
    );
    expect(within(dialog).getAllByTestId('purge-plan-item')[1]).toHaveTextContent(
      'skipped. Job job_9 is working on it.',
    );
    expect(screen.getByTestId('purge-confirm')).toHaveTextContent('Delete 1 dataset permanently');
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(second.calls)).toEqual([{ items: [{ kind: 'dataset', id: 'd-a' }] }]);
    expect(purges(calls)).toEqual([]);
  });
});

describe('Label runs, generation runs and requests', () => {
  beforeEach(() => {
    useLabelRunsStore.setState({
      list: [],
      byId: {},
      selectedId: null,
      error: null,
      labels: {},
      live: {},
      includeArchived: true,
    });
    useGenerationStore.setState({
      list: [],
      byId: {},
      records: {},
      pairs: {},
      live: {},
      selectedId: null,
      error: null,
      includeArchived: true,
    });
    useApprovalsStore.setState({ history: [], historyIncludeArchived: true, historyError: null });
  });

  it('purges the selected archived label runs and closes a purged open run', async () => {
    let gone = new Set<string>();
    const routes = purgeRoutes();
    const runs = [
      run({ id: 'lr_1', state: 'completed', ...ARCHIVED }),
      run({ id: 'lr_2', state: 'completed', ...ARCHIVED }),
      run({ id: 'lr_3', state: 'completed', ...SHOWN }),
    ];
    const { calls } = mockFetch({
      'GET /api/v1/label-runs': () => ({
        json: { items: runs.filter((r) => !gone.has(r.id)), total: 3, page: 1, limit: 50 },
      }),
      'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 },
      'POST /api/v1/purge/plan': routes['POST /api/v1/purge/plan'],
      'POST /api/v1/purge': (body: unknown) => {
        const out = routes['POST /api/v1/purge'](body);
        gone = new Set(out.json.items.map((o) => o.id));
        return out;
      },
    });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    fireEvent.click((await screen.findAllByTestId('label-run-card'))[0]);
    await screen.findByTestId('purge-bar');
    expect(screen.getAllByTestId('purge-checkbox')).toHaveLength(2);
    fireEvent.click(screen.getByTestId('purge-select-all'));
    fireEvent.click(screen.getByTestId('purge-open'));
    await screen.findByTestId('purge-plan');
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(calls)[0].items).toEqual([
      { kind: 'label_run', id: 'lr_1' },
      { kind: 'label_run', id: 'lr_2' },
    ]);
    await waitFor(() => expect(screen.getAllByTestId('label-run-card')).toHaveLength(1));
    expect(useLabelRunsStore.getState().selectedId).toBeNull();
  });

  it('purges an archived generation run', async () => {
    const routes = purgeRoutes();
    const gen = { ...useGenerationFixture(), ...ARCHIVED };
    const { calls } = mockFetch({
      'GET /api/v1/generation-runs': { items: [gen], total: 1, page: 1, limit: 50 },
      ...routes,
    });
    render(<GenerationPanel panel={getPanel('generation')} />);
    fireEvent.click(
      await screen.findByRole('checkbox', {
        name: 'Select generation run gr_1 for permanent delete',
      }),
    );
    expect(screen.getByTestId('purge-count')).toHaveTextContent('1 selected');
    fireEvent.click(screen.getByTestId('purge-open'));
    await screen.findByTestId('purge-plan');
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(calls)[0].items).toEqual([{ kind: 'generation_run', id: 'gr_1' }]);
  });

  it('purges an archived earlier request; a shown one has no checkbox', async () => {
    const routes = purgeRoutes();
    const approval = (id: string, extra: Partial<Approval>): Approval => ({
      id,
      action: 'version_delete',
      target: 't',
      summary: 's',
      payload: {},
      request_digest: '0'.repeat(64),
      requested_by: 'agent:dataworks-mcp',
      status: 'rejected',
      expires_at: '2026-10-21T12:00:00Z',
      decided_by: 'Sean',
      decided_at: null,
      reason: null,
      created_at: '2026-10-20T12:00:00Z',
      ...SHOWN,
      ...extra,
    });
    const { calls } = mockFetch({
      'GET /api/v1/approvals': { approvals: [approval('apr_a', ARCHIVED), approval('apr_s', {})] },
      ...routes,
    });
    render(<ApprovalHistoryCard />);
    fireEvent.click(
      await screen.findByRole('checkbox', { name: 'Select request apr_a for permanent delete' }),
    );
    expect(screen.getAllByTestId('purge-checkbox')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('purge-open'));
    await screen.findByTestId('purge-plan');
    fireEvent.click(screen.getByTestId('purge-confirm'));
    await screen.findByTestId('purge-results');
    expect(purges(calls)[0].items).toEqual([{ kind: 'approval', id: 'apr_a' }]);
  });
});

describe('purge results', () => {
  // The operator's batch of 2026-10-21 00:17 UTC as the fixed backend answers it when its
  // datasets go first: 9 datasets purged, 1 refused, and 15 runs and requests their datasets
  // took with them. Production answered these 15 "blocked, not_found" and the dialog said
  // "Deleted 9 of 25".
  const dataset = (id: string, over: Partial<PurgeOutcome> = {}) =>
    outcome({ kind: 'dataset', id }, { status: 'purged', tombstone_id: `prg_${id}`, ...over });
  const taken = (kind: 'label_run' | 'approval', id: string, parent: string) =>
    outcome(
      { kind, id },
      {
        name: null,
        status: 'purged',
        code: 'removed_with',
        counts: {},
        bytes: 0,
        paths: 0,
        tombstone_id: `prg_${parent}`,
        removed_with: { kind: 'dataset', id: parent, name: `name-${parent}` },
      },
    );
  const live: PurgeOutcome[] = [
    ...['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7', 'd9', 'd10'].map((id) => dataset(id)),
    dataset('d8', {
      status: 'blocked',
      code: 'purge_blocked',
      message: 'publish record pub_1 still uses this version.',
      counts: {},
      bytes: 0,
    }),
    ...Array.from({ length: 13 }, (_, i) => taken('label_run', `lr_${i}`, 'd5')),
    taken('approval', 'apr_1', 'd5'),
    taken('approval', 'apr_2', 'd10'),
  ];

  it('counts what went with its dataset as deleted, and says so', () => {
    expect(live).toHaveLength(25);
    expect(describeResults(live)).toBe(
      'Deleted 24 of 25 (15 with the item they belonged to); freed 18.0 KB.',
    );
    expect(describeResult(live[11])).toBe('deleted with dataset name-d5, whose record counts it');
    expect(describeResult(live[9])).toBe(
      'not deleted. publish record pub_1 still uses this version.',
    );
    expect(describeResult(live[0])).toBe('deleted (2 versions, 30 labels; 2.0 KB)');
  });

  it('still says not deleted for an id nothing removed', () => {
    const missing = outcome(
      { kind: 'label_run', id: 'lr_missing' },
      { status: 'blocked', code: 'not_found', message: 'No label run lr_missing.' },
    );
    expect(describeResults([missing])).toBe('Deleted 0 of 1; freed 0 B.');
    expect(describeResult(missing)).toBe('not deleted. No label run lr_missing.');
  });
});

describe('describeCounts', () => {
  it('names what goes, most familiar first, singular and plural', () => {
    expect(describeCounts({ row_events: 12, versions: 1, labels: 2, mystery_rows: 3 })).toBe(
      '1 version, 2 labels, 12 row events, 3 mystery rows',
    );
  });
});

function useGenerationFixture() {
  return {
    id: 'gr_1',
    mode: 'standard',
    target_type: 'sft',
    state: 'completed',
    input_version_id: 'v1234567890',
    held_out_splits: ['test'],
    prompt_column: 'prompt',
    seed_splits: ['train'],
    sample_size: 10,
    seed: 3,
    n_responses: 1,
    stages: ['respond'],
    generation_endpoint: { model_id: 'Qwen2.5-7B-Instruct' },
    server_kind: 'generic',
    generator_identities: [],
    judge_identity: null,
    chosen_side: null,
    engine_path: 'native',
    pinned: true,
    revision_reported: true,
    model_revision: 'abc',
    failure_reason: null,
    error: null,
    warnings: [],
    counts: {},
    snapshots: [],
    started_by: 'Ada',
    started_by_origin: 'operator',
    created_at: '',
    completed_at: '',
    job_ids: [],
    current_job_id: null,
    room: 'dataworks/generation-runs/gr_1',
    resumable: false,
  };
}

// Archive (operator decision 2026-10-20): the real panels, stores and API client against a fetch
// stub. Each test clicks through from the screen, so removing a toggle, an Archive button, the
// store call or the include_archived parameter turns it red.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApprovalHistoryCard } from '@/components/approvals/ApprovalHistoryCard';
import { DatasetsPanel } from '@/components/panels/DatasetsPanel';
import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { getPanel } from '@/config/panels';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { mockFetch } from '@/test/fetchMock';
import { META } from '@/test/fixtures001';
import { DATASET } from '@/test/fixtures002';
import { run } from '@/test/fixtures005';
import type { Approval } from '@/types/api';
import type { GenerationRun } from '@/types/generation';

type Call = { method: string; path: string; body: unknown };
// The purge plan (read-only) is asked whenever archived items are shown; these tests are about archive.
const posts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.path !== '/api/v1/purge/plan').map((c) => c.path);
const gets = (calls: Call[], base: string) => calls.filter((c) => c.method === 'GET' && c.path.split('?')[0] === base).map((c) => c.path);
const ARCHIVED = { archived_at: '2026-10-20T14:05:00Z', archived_by: 'Sean', archive_reason: null };
const SHOWN = { archived_at: null, archived_by: null, archive_reason: null };

afterEach(() => vi.unstubAllGlobals());

describe('Datasets screen', () => {
  beforeEach(() => useSourcesStore.setState({ meta: META, sources: [] }));

  const page = (items: unknown[]) => ({ items, total: items.length, page: 1, limit: 200 });

  it('lists without archived datasets until Show archived is on, then asks for them', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/datasets': page([{ ...DATASET, ...SHOWN }]),
      'GET /api/v1/sources/meta': META,
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
    });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    await screen.findByTestId('dataset-card');
    expect(gets(calls, '/api/v1/datasets').every((p) => !p.includes('include_archived'))).toBe(true);
    fireEvent.click(screen.getByTestId('show-archived-datasets'));
    await waitFor(() => expect(gets(calls, '/api/v1/datasets').some((p) => p.includes('include_archived=true'))).toBe(true));
  });

  it('archives a dataset from its card and reloads the list', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/datasets': page([{ ...DATASET, ...SHOWN }]),
      'GET /api/v1/sources/meta': META,
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      [`POST /api/v1/datasets/${DATASET.id}/archive`]: { ...DATASET, ...ARCHIVED, version_list: [] },
    });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    const before = gets(calls, '/api/v1/datasets').length;
    fireEvent.click(await screen.findByRole('button', { name: `Archive dataset ${DATASET.name}` }));
    await waitFor(() => expect(posts(calls)).toEqual([`/api/v1/datasets/${DATASET.id}/archive`]));
    await waitFor(() => expect(gets(calls, '/api/v1/datasets').length).toBe(before + 1));
  });

  it('an archived card says so and offers Unarchive', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/datasets': page([{ ...DATASET, ...ARCHIVED }]),
      'GET /api/v1/sources/meta': META,
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      [`POST /api/v1/datasets/${DATASET.id}/unarchive`]: { ...DATASET, ...SHOWN, version_list: [] },
    });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    expect(await screen.findByTestId('archived-pill')).toHaveTextContent('Archived');
    expect(screen.getByTestId('archived-note')).toHaveTextContent('Archived Oct 20, 2026, 10:05 AM ET by Sean.');
    fireEvent.click(screen.getByRole('button', { name: `Unarchive dataset ${DATASET.name}` }));
    await waitFor(() => expect(posts(calls)).toEqual([`/api/v1/datasets/${DATASET.id}/unarchive`]));
  });

  it('shows the refusal when a job is working on the dataset', async () => {
    mockFetch({
      'GET /api/v1/datasets': page([{ ...DATASET, ...SHOWN }]),
      'GET /api/v1/sources/meta': META,
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      [`POST /api/v1/datasets/${DATASET.id}/archive`]: () => ({
        status: 409,
        json: { error: { code: 'dataset_busy', message: 'Job job_1 is working on humor. Wait for it to finish or cancel it, then archive the dataset.', details: {} } },
      }),
    });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    fireEvent.click(await screen.findByRole('button', { name: `Archive dataset ${DATASET.name}` }));
    expect(await screen.findByText(/Job job_1 is working on humor/)).toBeInTheDocument();
  });
});

describe('Label runs screen', () => {
  beforeEach(() => useLabelRunsStore.setState({ list: [], byId: {}, selectedId: null, error: null, labels: {}, live: {}, includeArchived: false }));

  it('asks for archived runs only with Show archived on', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/label-runs': { items: [], total: 0, page: 1, limit: 50 } });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    await waitFor(() => expect(gets(calls, '/api/v1/label-runs')).toEqual(['/api/v1/label-runs']));
    fireEvent.click(screen.getByTestId('show-archived-label-runs'));
    await waitFor(() => expect(gets(calls, '/api/v1/label-runs')).toEqual(['/api/v1/label-runs', '/api/v1/label-runs?include_archived=true']));
  });

  it('archives a finished run from its detail and reloads the list', async () => {
    const done = run({ state: 'completed', ...SHOWN });
    let archived = false; // the default list leaves an archived run out, as the backend does
    const { calls } = mockFetch({
      'GET /api/v1/label-runs': () => ({ json: { items: archived ? [] : [done], total: archived ? 0 : 1, page: 1, limit: 50 } }),
      'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 },
      'POST /api/v1/label-runs/lr_1/archive': () => {
        archived = true;
        return { json: { ...done, ...ARCHIVED } };
      },
    });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    fireEvent.click(await screen.findByTestId('label-run-card'));
    const detail = screen.getByTestId('label-run-detail');
    fireEvent.click(within(detail).getByRole('button', { name: 'Archive label run' }));
    await waitFor(() => expect(posts(calls)).toEqual(['/api/v1/label-runs/lr_1/archive']));
    await waitFor(() => expect(gets(calls, '/api/v1/label-runs')).toHaveLength(2));
    expect(await within(detail).findByRole('button', { name: 'Unarchive label run' })).toBeInTheDocument();
  });

  it('offers no Archive on a run that is still running', async () => {
    mockFetch({
      'GET /api/v1/label-runs': { items: [run({ state: 'running' })], total: 1, page: 1, limit: 50 },
      'GET /api/v1/label-runs/lr_1/labels': { items: [], total: 0, page: 1, limit: 500 },
    });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    fireEvent.click(await screen.findByTestId('label-run-card'));
    expect(within(screen.getByTestId('label-run-detail')).queryByRole('button', { name: /archive/i })).toBeNull();
  });
});

const GEN: GenerationRun = {
  id: 'gr_1', mode: 'standard', target_type: 'sft', state: 'failed', input_version_id: 'v1234567890',
  held_out_splits: ['test'], prompt_column: 'prompt', seed_splits: ['train'], sample_size: 10, seed: 3,
  n_responses: 1, stages: ['respond'], generation_endpoint: { model_id: 'Qwen2.5-7B-Instruct' },
  server_kind: 'generic', generator_identities: [], judge_identity: null, chosen_side: null,
  engine_path: 'native', pinned: true, revision_reported: true, model_revision: 'abc', failure_reason: null,
  error: null, warnings: [], counts: {}, snapshots: [], started_by: 'Ada', started_by_origin: 'operator',
  created_at: '', completed_at: '', job_ids: [], current_job_id: null, room: 'dataworks/generation-runs/gr_1', resumable: false,
  ...SHOWN,
};

describe('Generation screen', () => {
  beforeEach(() => useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, selectedId: null, error: null, includeArchived: false }));

  it('asks for archived runs only with Show archived on, and archives from the detail', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/generation-runs': { items: [GEN], total: 1, page: 1, limit: 50 },
      'GET /api/v1/generation-runs/gr_1/records': { items: [], total: 0, page: 1, limit: 50 },
      'POST /api/v1/generation-runs/gr_1/archive': { ...GEN, ...ARCHIVED },
    });
    render(<GenerationPanel panel={getPanel('generation')} />);
    fireEvent.click(await screen.findByTestId('generation-run-card'));
    fireEvent.click(within(screen.getByTestId('generation-run-detail')).getByRole('button', { name: 'Archive generation run' }));
    await waitFor(() => expect(posts(calls)).toEqual(['/api/v1/generation-runs/gr_1/archive']));
    expect(gets(calls, '/api/v1/generation-runs').every((p) => !p.includes('include_archived'))).toBe(true);
    fireEvent.click(screen.getByTestId('show-archived-generation-runs'));
    await waitFor(() => expect(gets(calls, '/api/v1/generation-runs').some((p) => p.includes('include_archived=true'))).toBe(true));
  });

  it('offers no Archive on a queued or running run', async () => {
    mockFetch({
      'GET /api/v1/generation-runs': { items: [{ ...GEN, state: 'running', current_job_id: 'job_1' }], total: 1, page: 1, limit: 50 },
      'GET /api/v1/generation-runs/gr_1/records': { items: [], total: 0, page: 1, limit: 50 },
    });
    render(<GenerationPanel panel={getPanel('generation')} />);
    fireEvent.click(await screen.findByTestId('generation-run-card'));
    expect(within(screen.getByTestId('generation-run-detail')).queryByRole('button', { name: /archive/i })).toBeNull();
  });
});

const approval = (id: string, status: Approval['status'], extra: Partial<Approval> = {}): Approval => ({
  id, action: 'version_delete', target: 'DELETE /api/v1/versions/{version_id}', summary: 's', payload: {},
  request_digest: '0'.repeat(64), requested_by: 'agent:dataworks-mcp', status, expires_at: '2026-10-21T12:00:00Z',
  decided_by: status === 'pending' || status === 'expired' ? null : 'Sean', decided_at: null, reason: null,
  created_at: '2026-10-20T12:00:00Z', ...SHOWN, ...extra,
});

describe('Earlier agent requests', () => {
  beforeEach(() => useApprovalsStore.setState({ history: [], historyIncludeArchived: false, historyError: null }));

  it('lists decided requests, offers Archive only on rejected and expired ones, and archives', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/approvals': {
        approvals: [approval('apr_p', 'pending'), approval('apr_r', 'rejected'), approval('apr_e', 'expired'), approval('apr_x', 'executed'), approval('apr_f', 'failed')],
      },
      'POST /api/v1/approvals/apr_r/archive': approval('apr_r', 'rejected', ARCHIVED),
    });
    render(<ApprovalHistoryCard />);
    const items = await screen.findAllByTestId('approval-history-item');
    expect(items).toHaveLength(4); // the pending one lives in the banner
    expect(screen.getAllByRole('button', { name: 'Archive request' })).toHaveLength(2);
    fireEvent.click(within(items[0]).getByRole('button', { name: 'Archive request' }));
    await waitFor(() => expect(posts(calls)).toEqual(['/api/v1/approvals/apr_r/archive']));
    await waitFor(() => expect(gets(calls, '/api/v1/approvals')).toHaveLength(2));
    expect(gets(calls, '/api/v1/approvals').every((p) => !p.includes('include_archived'))).toBe(true);
  });

  it('asks for archived requests with Show archived on and unarchives one', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/approvals': { approvals: [approval('apr_r', 'rejected', ARCHIVED)] },
      'POST /api/v1/approvals/apr_r/unarchive': approval('apr_r', 'rejected'),
    });
    useApprovalsStore.setState({ historyIncludeArchived: true });
    render(<ApprovalHistoryCard />);
    await waitFor(() => expect(gets(calls, '/api/v1/approvals')).toEqual(['/api/v1/approvals?include_archived=true']));
    fireEvent.click(await screen.findByRole('button', { name: 'Unarchive request' }));
    await waitFor(() => expect(posts(calls)).toEqual(['/api/v1/approvals/apr_r/unarchive']));
    fireEvent.click(screen.getByTestId('show-archived-approvals'));
    await waitFor(() => expect(gets(calls, '/api/v1/approvals').at(-1)).toBe('/api/v1/approvals'));
  });
});

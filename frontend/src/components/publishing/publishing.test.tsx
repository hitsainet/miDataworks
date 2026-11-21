// Feature 008's screen (FTASKS 11.3, 11.6): the button rule, the store's invalidation, the panel's
// registration, and a private publish through the real store and API client over a fetch stub.
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { PublishPanel } from '@/components/panels/PublishPanel';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { getPanel } from '@/config/panels';
import { usePublishStore } from '@/stores/publishStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import type { CheckOutcome, CheckRun } from '@/types/publishing';

import { publishGate } from './publishRules';

const o = (check: string, outcome: CheckOutcome['outcome'], extra: Partial<CheckOutcome> = {}): CheckOutcome => ({
  check, outcome, reason: `${check} reason.`, next_step: `${check} next.`, evidence: {}, ...extra,
});
const run = (results: CheckOutcome[], status = 'completed'): CheckRun => ({
  id: 'pchk_1', version_id: 'v1', build_id: 'pbld_1', repo_id: 'mistudio/humor', requested_visibility: 'private',
  status, results, licence_table_version: 1, job_id: 'job_c', created_at: '', completed_at: status === 'completed' ? '' : null,
});

describe('publishGate', () => {
  it('names the action by visibility', () => {
    expect(publishGate(run([o('C-1', 'green')]), 'private', false).label).toBe('Publish privately');
    expect(publishGate(run([o('C-1', 'green')]), 'public', false).label).toBe('Publish publicly');
  });
  it('waits for checks', () => {
    expect(publishGate(null, 'private', false)).toMatchObject({ disabled: true });
    expect(publishGate(run([], 'queued'), 'private', false).disabled).toBe(true);
  });
  it('amber blocks public only and the tooltip names the first blocker', () => {
    const amber = run([o('C-1', 'green'), o('C-7', 'amber'), o('C-5', 'amber')]);
    expect(publishGate(amber, 'private', false).disabled).toBe(false);
    const gate = publishGate(amber, 'public', false);
    expect(gate.disabled).toBe(true);
    expect(gate.blocker).toBe('C-7: C-7 reason. C-7 next.');
  });
  it('refused blocks every push', () => {
    expect(publishGate(run([o('C-2', 'refused')]), 'private', false).disabled).toBe(true);
  });
  it('a public repository makes a private choice public (EC-1)', () => {
    const repo = o('repository', 'note', { evidence: { effective_visibility: 'public' } });
    const gate = publishGate(run([o('C-7', 'amber'), repo]), 'private', false);
    expect(gate.label).toBe('Publish publicly');
    expect(gate.disabled).toBe(true);
  });
  it('notes never block', () => {
    expect(publishGate(run([o('N-unpinned_labeler', 'note')]), 'public', false).disabled).toBe(false);
  });
});

describe('publishStore invalidation', () => {
  beforeEach(() => usePublishStore.setState({ checkRun: run([o('C-1', 'green')]), build: null, selection: { versionId: 'v1', repoId: 'a/b', visibility: 'private', prose: '', labelColumn: '' } }));
  it.each([
    [{ visibility: 'public' as const }],
    [{ repoId: 'a/c' }],
    [{ prose: 'new words' }],
    [{ versionId: 'v2' }],
    [{ labelColumn: 'label' }],
  ])('any selection change clears the check run (%o)', (patch) => {
    usePublishStore.getState().setSelection(patch);
    expect(usePublishStore.getState().checkRun).toBeNull();
  });
  it('an unchanged value keeps it', () => {
    usePublishStore.getState().setSelection({ repoId: 'a/b' });
    expect(usePublishStore.getState().checkRun).not.toBeNull();
  });
});

const BUILD = {
  id: 'pbld_1', version_id: 'v1', status: 'completed', projection: {}, job_id: null, created_at: '', completed_at: '',
  files: [{ name: 'train', path: 'data/train.parquet', rows: 2208, bytes: 181234, sha256: 'a'.repeat(64), git_blob_sha1: 'b'.repeat(40), logical_digest: 'c'.repeat(64), label_counts: {}, held_out: false, evaluation_only: false }],
  columns: [], omitted: {}, error: null,
};
const ROUTES = {
  'GET /api/health': HEALTH,
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/versions': { items: [{ id: 'v1', dataset_id: 'd1', dataset_name: 'humor', target_type: 'detector', number: 1, state: 'completed', is_head: true }], total: 1 },
  'GET /api/v1/publishes': { items: [], total: 0 },
  'GET /api/v1/exports': { items: [], total: 0 },
  'POST /api/v1/versions/v1/publish-builds': { build_id: 'pbld_1', job_id: null, reused: true, status: 'completed' },
  'GET /api/v1/publish-builds/pbld_1': BUILD,
  'GET /api/v1/versions/v1/card-draft': { front_matter: { license: 'cc-by-2.0' }, record_markdown: '<!-- dw:record -->', prose: '# humor v1', build_id: 'pbld_1' },
  'POST /api/v1/versions/v1/publish-checks': { check_run_id: 'pchk_1', job_id: 'job_c' },
  'GET /api/v1/publish-check-runs/pchk_1': run([o('C-1', 'green'), o('C-2', 'green'), o('C-7', 'amber', { reason: 'The shortcut audit has not been checked.' })]),
  'GET /api/v1/jobs/job_c': { id: 'job_c', status: 'completed', progress: 100, message: null },
  'POST /api/v1/publishes': { publish_id: 'pub_1', job_id: 'job_p', request_digest: 'd'.repeat(64) },
};

describe('PublishPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    usePublishStore.setState({ build: null, checkRun: null, draft: null, job: null, lastPublish: null, error: null, notice: null, selection: { versionId: '', repoId: '', visibility: 'private', prose: '', labelColumn: '' } });
    window.location.hash = '';
  });

  it('is the registered publish screen and the shell renders it', async () => {
    mockFetch(ROUTES);
    expect(PANEL_COMPONENTS.publish).toBe(PublishPanel);
    window.location.hash = '#/publish';
    render(<App />);
    expect(screen.queryByText('This screen is not built yet')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Publish to Hugging Face')).toBeInTheDocument());
  });

  it('previews files, runs checks, keeps public disabled on amber and publishes privately', async () => {
    const { calls } = mockFetch(ROUTES);
    const user = userEvent.setup();
    render(<PublishPanel panel={getPanel('publish')} />);
    await waitFor(() => expect(screen.getByRole('option', { name: 'humor v1' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Version'), 'v1');
    await user.type(screen.getByLabelText('Repository'), 'mistudio/humor');
    await user.click(screen.getByRole('button', { name: 'Preview files' }));
    await waitFor(() => expect(screen.getByTestId('file-preview')).toHaveTextContent('2,208'));
    await waitFor(() => expect(screen.getByTestId('check-C-7')).toHaveAttribute('data-outcome', 'amber'), { timeout: 3000 });
    expect(screen.getByTestId('check-C-7')).toHaveTextContent('Blocks a public push');
    await user.selectOptions(screen.getByLabelText('Visibility'), 'public');
    await waitFor(() => expect(screen.getByTestId('check-C-7')).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByRole('button', { name: 'Publish publicly' })).toBeDisabled();
    expect(screen.getByTestId('publish-blocker').getAttribute('data-blocker')).toContain('C-7');
    await user.selectOptions(screen.getByLabelText('Visibility'), 'private');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publish privately' })).toBeEnabled(), { timeout: 3000 });
    await act(async () => user.click(screen.getByRole('button', { name: 'Publish privately' })));
    const sent = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/publishes');
    expect(sent?.body).toEqual({ version_id: 'v1', build_id: 'pbld_1', repo_id: 'mistudio/humor', visibility: 'private', card_prose: '' });
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/publishes')).toHaveLength(1);
  });
});

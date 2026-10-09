// Feature 001's components (FTASKS 11.3–11.10) against the REAL store and API client, with fetch
// answered by route (no hand-mocked store: the wiring under test is the one that ships).
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { getPanel } from '@/config/panels';
import { DatasetsPanel } from '@/components/panels/DatasetsPanel';
import { useDraftsStore } from '@/stores/draftsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { DETAIL, META, MULTI_CONFIG, PREVIEW, SOURCE, UPLOAD_SOURCE } from '@/test/fixtures001';
import { mockFetch } from '@/test/fetchMock';
import type { RecipeDraft } from '@/types/recipes';

import { CommitChip } from './CommitChip';
import { DetectionPanel, UNDETECTED_ADVICE } from './DetectionPanel';
import { ImportForm } from './ImportForm';
import { LicenceBadge, NOT_STATED_ADVICE } from './LicenceBadge';
import { SourceDetailDrawer } from './SourceDetailDrawer';
import { SourcesList } from './SourcesList';
import { buildManifest, UploadCard } from './UploadCard';

const TOKEN = 'hf_form_token_3';
const initial = useSourcesStore.getState();
const LIST = { items: [SOURCE, UPLOAD_SOURCE], total: 2, page: 1, limit: 100 };
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => useSourcesStore.setState({ ...initial, imports: {}, sources: [], selected: null, meta: META, preview: null, previewStatus: 'idle' }, true));
afterEach(() => vi.unstubAllGlobals());

describe('ImportForm', () => {
  it('maps every field to the request, in the mockup order, and clears the token after the request', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/sources/hf': { job_id: 'job_5', source_id: null, existing_job: false }, 'GET /api/v1/sources': LIST });
    render(<ImportForm />);
    const labels = screen.getAllByText(/^(Repository ID|Split \(optional\)|Config \(optional\)|Revision|Access token \(optional\))$/).map((n) => n.textContent);
    expect(labels).toEqual(['Repository ID', 'Split (optional)', 'Config (optional)', 'Revision', 'Access token (optional)']);
    type('Repository ID', 'tasksource/humicroedit');
    type('Split (optional)', 'train');
    type('Config (optional)', 'subtask-1');
    type('Revision', 'f5a16e65');
    type('Access token (optional)', TOKEN);
    expect(screen.getByLabelText('Access token (optional)')).toHaveAttribute('autocomplete', 'off');
    expect(screen.getByLabelText('Access token (optional)')).toHaveAttribute('data-1p-ignore', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/sources/hf')).toBe(true));
    expect(calls.find((c) => c.path === '/api/v1/sources/hf')?.body).toEqual({ repo_id: 'tasksource/humicroedit', split: 'train', config: 'subtask-1', revision: 'f5a16e65', access_token: TOKEN });
    expect(screen.getByLabelText('Access token (optional)')).toHaveValue('');
  });

  it('clears the token after a FAILED request too', async () => {
    mockFetch({ 'POST /api/v1/sources/hf/preview': () => ({ status: 401, json: { error: { code: 'hf_token_rejected', message: 'Hugging Face rejected the per-import token.', details: {} } } }) });
    render(<ImportForm />);
    type('Repository ID', 'org/private');
    type('Access token (optional)', TOKEN);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('Hugging Face rejected the per-import token.')).toBeInTheDocument();
    expect(screen.getByLabelText('Access token (optional)')).toHaveValue('');
  });

  it('refuses a malformed repository ID beside the field without calling the API', () => {
    const { calls } = mockFetch({});
    render(<ImportForm />);
    type('Repository ID', 'colbert');
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(screen.getByRole('alert')).toHaveTextContent('owner/name');
    expect(screen.getByLabelText('Repository ID')).toHaveAttribute('aria-invalid', 'true');
    expect(calls).toEqual([]);
  });
});

describe('PreviewModal', () => {
  it('shows the commit, head note, sample table with truncation, detection and unavailable parts; a chosen split fills the form', async () => {
    mockFetch({ 'POST /api/v1/sources/hf/preview': PREVIEW });
    render(<ImportForm />);
    type('Repository ID', PREVIEW.repo_id);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    const modal = await screen.findByTestId('preview-modal');
    await within(modal).findByTestId('sample-table');
    expect(within(modal).getByText('2bb7d6bc…')).toBeInTheDocument();
    expect(within(modal).getByTestId('head-note')).toHaveTextContent('branch head');
    expect(within(modal).getByTestId('sample-table')).toHaveTextContent('A very long joke… (5,000 characters)');
    expect(within(modal).getByTestId('unavailable')).toHaveTextContent('could not report the size');
    expect(within(modal).getByTestId('detection-panel')).toHaveTextContent('Columns text and humor');
    expect(within(modal).getByText(/200,000 rows in 1 split, 10\.4 MB Parquet \(Hub-reported\)/)).toBeInTheDocument();
    fireEvent.click(within(modal).getByLabelText('Choose split train'));
    fireEvent.click(screen.getByRole('button', { name: 'Use this split' }));
    expect(screen.getByLabelText('Split (optional)')).toHaveValue('train');
  });

  it('lists a multi-config repository\'s configs, chooses none, and previews the one picked', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/sources/hf/preview': (body: unknown) => ({ json: (body as { config?: string }).config ? { ...PREVIEW, config: 'subtask-2' } : MULTI_CONFIG }) });
    render(<ImportForm />);
    type('Repository ID', MULTI_CONFIG.repo_id);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByText(/has 2 configs\. Choose one/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import' }, )).not.toBeNull(); // the form's own button
    expect(within(screen.getByTestId('preview-modal')).queryByTestId('sample-table')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Use config subtask-2' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/preview')).length).toBe(2));
    expect(calls[1].body).toMatchObject({ config: 'subtask-2' });
    expect(screen.getByLabelText('Config (optional)')).toHaveValue('subtask-2');
  });
});

describe('UploadCard', () => {
  it('builds the manifest with a split per file and shows the limit from meta', () => {
    const a = new File(['1'], 'train.parquet');
    const b = new File(['2'], 'test.csv');
    expect(buildManifest([{ file: a, split: 'train' }, { file: b, split: ' test ' }], META.csv_defaults, ' Jokes ')).toEqual({
      files: [{ name: 'train.parquet', split: 'train' }, { name: 'test.csv', split: 'test' }],
      csv: META.csv_defaults,
      display_name: 'Jokes',
    });
    expect(buildManifest([{ file: a, split: 'train' }], META.csv_defaults, '')).toEqual({ files: [{ name: 'train.parquet', split: 'train' }] });
    render(<UploadCard />);
    expect(screen.getByText(/Up to 2\.0 GB per file/)).toBeInTheDocument();
  });

  it('names its action with the count and shows the 413 message', async () => {
    mockFetch({ 'POST /api/v1/sources/uploads': () => ({ status: 413, json: { error: { code: 'upload_too_large', message: 'big.csv is over the 2,147,483,648-byte upload limit. Split it, or raise the limit in Settings → Storage.', details: {} } } }) });
    render(<UploadCard />);
    const input = screen.getByLabelText('Choose files to upload');
    fireEvent.change(input, { target: { files: [new File(['a'], 'big.csv'), new File(['b'], 'more.jsonl')] } });
    expect(screen.getByLabelText('Split name for big.csv')).toHaveValue('train');
    expect(screen.getByLabelText('Split name for more.jsonl')).toHaveValue('test');
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2 files' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('raise the limit in Settings');
  });
});

describe('SourcesList and the detail drawer', () => {
  it('lists states in words, short identifiers and licences, and opens a source', async () => {
    mockFetch({ [`GET /api/v1/sources/${SOURCE.id}`]: DETAIL });
    useSourcesStore.setState({ sources: [SOURCE, UPLOAD_SOURCE] });
    render(<><SourcesList onOpen={(s) => void useSourcesStore.getState().fetchSource(s.id)} /><SourceDetailDrawer /></>);
    const list = screen.getByTestId('sources-list');
    expect(list).toHaveAttribute('open');
    expect(within(list).getByText('Ready')).toBeInTheDocument();
    expect(within(list).getByText('Importing')).toBeInTheDocument();
    expect(within(list).getByText('2bb7d6bc…')).toBeInTheDocument();
    expect(within(list).getByText('Licence: not stated')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: `Open source ${SOURCE.display_name}` }));
    const drawer = await screen.findByTestId('source-drawer');
    expect(within(drawer).getByText('Build a version from this source')).toBeInTheDocument();
  });

  it('is collapsed when every source is ready', () => {
    useSourcesStore.setState({ sources: [SOURCE] });
    render(<SourcesList onOpen={() => undefined} />);
    expect(screen.getByTestId('sources-list')).not.toHaveAttribute('open');
  });

  it('records a terms annotation from the drawer and refetches the source', async () => {
    const { calls } = mockFetch({ [`POST /api/v1/sources/${SOURCE.id}/annotations`]: () => ({ status: 201, json: {} }), [`GET /api/v1/sources/${SOURCE.id}`]: DETAIL });
    useSourcesStore.setState({ selected: DETAIL });
    render(<SourceDetailDrawer />);
    fireEvent.change(screen.getByLabelText('Redistribution'), { target: { value: 'private_only' } });
    type('Reason (where you read it)', 'card says NC');
    fireEvent.click(screen.getByRole('button', { name: 'Record the terms' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'GET')).toBe(true));
    expect(calls[0].body).toEqual({ kind: 'terms', redistribution: 'private_only', value: {}, reason: 'card says NC' });
  });

  it('deletes only after a confirmation naming the consequence', async () => {
    const { calls } = mockFetch({ [`DELETE /api/v1/sources/${SOURCE.id}`]: { ...DETAIL, state: 'deleted' }, 'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 } });
    useSourcesStore.setState({ selected: DETAIL });
    render(<SourceDetailDrawer />);
    fireEvent.click(screen.getByRole('button', { name: 'Delete the source' }));
    expect(screen.getByTestId('delete-confirm')).toHaveTextContent('record, hashes and licence notes stay');
    expect(calls).toEqual([]);
    type('Reason for deleting', 'duplicate');
    fireEvent.click(screen.getByRole('button', { name: 'Delete its files' }));
    await waitFor(() => expect(calls[0]).toMatchObject({ method: 'DELETE', body: { reason: 'duplicate' } }));
  });

  it('"Build a version" hands the source and detection to the draft once and opens New dataset', async () => {
    const draft: RecipeDraft = { id: 'd1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'import', choices: {} }, updated_by: 'Ada', updated_at: '' };
    const updateDraft = vi.fn();
    useDraftsStore.setState({ loadDraft: async () => draft, updateDraft });
    useSourcesStore.setState({ selected: DETAIL });
    render(<SourceDetailDrawer />);
    fireEvent.click(screen.getByRole('button', { name: 'Build a version from this source' }));
    await waitFor(() => expect(updateDraft).toHaveBeenCalledTimes(1));
    expect(updateDraft).toHaveBeenCalledWith({ inputs: [{ kind: 'source', source_id: SOURCE.id }], flow_state: { step: 'import', choices: { source_id: SOURCE.id, detection: DETAIL.detection } } });
    expect(window.location.hash).toBe('#/new-dataset');
  });
});

describe('small parts', () => {
  it('CommitChip shortens to 8 characters with the full value on hover and a copy button', () => {
    render(<CommitChip value={SOURCE.resolved_commit} />);
    expect(screen.getByText('2bb7d6bc…')).toBeInTheDocument();
    expect(screen.getByTitle(SOURCE.resolved_commit!)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy/ })).toBeInTheDocument();
  });

  it('LicenceBadge says what to do when no licence is stated', () => {
    render(<LicenceBadge display="not stated" verbose />);
    expect(screen.getByText(NOT_STATED_ADVICE)).toBeInTheDocument();
    expect(NOT_STATED_ADVICE).toMatch(/record its terms before publishing publicly/);
  });

  it('DetectionPanel says what to do when nothing was detected', () => {
    render(<DetectionPanel detection={{ ...DETAIL.detection!, trl_type: 'undetected', reasons: [{ output: 'trl_type', reason: 'Ambiguous: preference' }] }} />);
    expect(screen.getByRole('status')).toHaveTextContent(UNDETECTED_ADVICE);
    expect(screen.getByText(/Ambiguous: preference/)).toBeInTheDocument();
  });
});

describe('guided Import step', () => {
  it("is discovered by 002's glob and hands a ready source to the draft once", async () => {
    const step = discoveredStepPanels().find((p) => p.step === 'import');
    expect(step).toBeDefined();
    mockFetch({ 'POST /api/v1/sources/hf': DETAIL, 'GET /api/v1/sources': LIST });
    const updateDraft = vi.fn();
    useDraftsStore.setState({ updateDraft });
    const draft: RecipeDraft = { id: 'd1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'import' }, updated_by: 'Ada', updated_at: '' };
    const onAdvance = vi.fn();
    const Step = step!.Component;
    render(<Step draft={draft} onAdvance={onAdvance} />);
    type('Repository ID', SOURCE.repo_id!);
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use this source' }));
    expect(updateDraft).toHaveBeenCalledTimes(1);
    expect(updateDraft).toHaveBeenCalledWith({ inputs: [{ kind: 'source', source_id: SOURCE.id }], flow_state: { step: 'import', choices: { source_id: SOURCE.id, detection: DETAIL.detection } } });
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });
});

describe('DatasetsPanel', () => {
  it('composes the form, upload, sources list and 002\'s grid, loading sources and meta', async () => {
    useSourcesStore.setState({ meta: null });
    const { calls } = mockFetch({ 'GET /api/v1/datasets': { items: [], total: 0, page: 1, limit: 50 }, 'GET /api/v1/sources/meta': META, 'GET /api/v1/sources': LIST });
    await act(async () => {
      render(<DatasetsPanel panel={getPanel('datasets')} />);
    });
    expect(screen.getByRole('heading', { level: 1, name: 'Datasets' })).toBeInTheDocument();
    expect(screen.getByText('Import from Hugging Face, curate and label into versions, publish back to the Hub')).toBeInTheDocument();
    expect(screen.getByText('Import from Hugging Face')).toBeInTheDocument();
    expect(screen.getByText('Upload a file')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(UPLOAD_SOURCE.display_name)).toBeInTheDocument());
    expect(calls.map((c) => `${c.method} ${c.path.split('?')[0]}`)).toEqual(expect.arrayContaining(['GET /api/v1/sources/meta', 'GET /api/v1/sources']));
  });
});

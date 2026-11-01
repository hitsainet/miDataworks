// sourcesStore (001 FTASKS 11.1, 11.6, 13.3): the token is sent once and never kept; completion
// REFETCHES rather than building a source from event fields; polling replaces the room while the
// socket is down; every call goes to /api/v1/sources/* (the browser never calls Hugging Face).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DETAIL, PREVIEW, SOURCE } from '@/test/fixtures001';
import { job, mockFetch } from '@/test/fetchMock';

import { useSourcesStore } from './sourcesStore';

const TOKEN = 'hf_per_import_secret_7';
const initial = useSourcesStore.getState();

beforeEach(() => useSourcesStore.setState({ ...initial, imports: {}, sources: [], selected: null, meta: null }, true));
afterEach(() => vi.unstubAllGlobals());

describe('sourcesStore', () => {
  it('sends the token once and never keeps it in state', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/sources/hf': { job_id: 'job_9', source_id: null, existing_job: false },
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      'POST /api/v1/sources/hf/preview': PREVIEW,
    });
    await useSourcesStore.getState().previewHf({ repo_id: PREVIEW.repo_id }, TOKEN);
    const outcome = await useSourcesStore.getState().importHf({ repo_id: ' org/data ', revision: '2bb7d6bc', split: '' }, TOKEN);
    expect(outcome).toEqual({ kind: 'started', jobId: 'job_9' });
    const post = calls.find((c) => c.path === '/api/v1/sources/hf');
    expect(post?.body).toEqual({ repo_id: 'org/data', revision: '2bb7d6bc', access_token: TOKEN });
    expect(JSON.stringify(useSourcesStore.getState())).not.toContain(TOKEN);
    expect(useSourcesStore.getState().imports.job_9.status).toBe('queued');
  });

  it('an already-imported full SHA answers with the source and says nothing was downloaded', async () => {
    mockFetch({ 'POST /api/v1/sources/hf': DETAIL, 'GET /api/v1/sources': { items: [SOURCE], total: 1, page: 1, limit: 100 } });
    const outcome = await useSourcesStore.getState().importHf({ repo_id: SOURCE.repo_id!, revision: SOURCE.resolved_commit! });
    expect(outcome.kind).toBe('existing');
    expect(useSourcesStore.getState().notice).toMatch(/Already imported.*Nothing was downloaded/);
  });

  it('an agent-gated request reports the approval, and a refusal keeps its code', async () => {
    mockFetch({
      'POST /api/v1/sources/hf': { approval_id: 'ap_1', status: 'pending', action: 'secret_write', hint: 'x' },
      'POST /api/v1/sources/uploads': () => ({ status: 413, json: { error: { code: 'upload_too_large', message: 'big.csv is over the limit.', details: {} } } }),
    });
    expect(await useSourcesStore.getState().importHf({ repo_id: 'org/data' })).toEqual({ kind: 'approval', approvalId: 'ap_1' });
    const refused = await useSourcesStore.getState().uploadFiles([new File(['x'], 'big.csv')], { files: [{ name: 'big.csv', split: 'train' }] });
    expect(refused).toMatchObject({ kind: 'refused', code: 'upload_too_large', message: 'big.csv is over the limit.' });
  });

  it('uploads multipart with the manifest and every file once', async () => {
    const { calls, fn } = mockFetch({ 'POST /api/v1/sources/uploads': { job_id: 'job_u', source_id: null, existing_job: false }, 'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 } });
    const manifest = { files: [{ name: 'a.parquet', split: 'train' }, { name: 'b.jsonl', split: 'test' }] };
    await useSourcesStore.getState().uploadFiles([new File(['1'], 'a.parquet'), new File(['2'], 'b.jsonl')], manifest);
    const upload = calls.find((c) => c.path === '/api/v1/sources/uploads');
    expect(upload?.body).toEqual({ formData: [['files', 'a.parquet'], ['files', 'b.jsonl'], ['manifest', JSON.stringify(manifest)]] });
    const init = fn.mock.calls.find(([p]) => p === '/api/v1/sources/uploads')?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });

  it('on completion it refetches the list and the open source, never building one from the event', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/sources': { items: [SOURCE], total: 1, page: 1, limit: 100 }, [`GET /api/v1/sources/${SOURCE.id}`]: DETAIL });
    useSourcesStore.setState({
      selected: { ...DETAIL, display_name: 'stale' },
      imports: { job_1: { jobId: 'job_1', label: 'x', status: 'running', phase: null, progress: 10, sourceId: null, existing: false, error: null } },
    });
    useSourcesStore.getState().applyImportEvent('source_import:completed', { job_id: 'job_1', source_id: SOURCE.id, display_name: 'from the event', existing: false });
    await vi.waitFor(() => expect(useSourcesStore.getState().selected?.display_name).toBe(DETAIL.display_name));
    expect(useSourcesStore.getState().sources).toEqual([SOURCE]);
    expect(calls.filter((c) => c.method === 'GET').map((c) => c.path.split('?')[0])).toEqual(['/api/v1/sources', `/api/v1/sources/${SOURCE.id}`]);
    expect(useSourcesStore.getState().imports.job_1).toMatchObject({ status: 'completed', sourceId: SOURCE.id });
  });

  it('ignores events for imports it does not track', () => {
    const { calls } = mockFetch({});
    useSourcesStore.getState().applyImportEvent('source_import:completed', { job_id: 'other', source_id: 'x' });
    expect(calls).toEqual([]);
  });

  it('polls the job record while the socket is down', async () => {
    mockFetch({ 'GET /api/v1/jobs/job_1': job({ id: 'job_1', kind: 'source_import', status: 'completed', progress: 100, message: 'Imported.', result: { source_id: SOURCE.id, existing: true } }), 'GET /api/v1/sources': { items: [SOURCE], total: 1, page: 1, limit: 100 } });
    useSourcesStore.setState({ imports: { job_1: { jobId: 'job_1', label: 'x', status: 'running', phase: null, progress: 0, sourceId: null, existing: false, error: null } } });
    await useSourcesStore.getState().pollImports();
    expect(useSourcesStore.getState().imports.job_1).toMatchObject({ status: 'completed', existing: true, sourceId: SOURCE.id, phase: 'Imported.' });
    expect(useSourcesStore.getState().sources).toEqual([SOURCE]);
  });

  it('annotates, overrides and deletes through the routes, refreshing what changed', async () => {
    const { calls } = mockFetch({
      [`POST /api/v1/sources/${SOURCE.id}/annotations`]: () => ({ status: 201, json: { id: 'a1' } }),
      [`GET /api/v1/sources/${SOURCE.id}`]: DETAIL,
      [`DELETE /api/v1/sources/${SOURCE.id}`]: { ...DETAIL, state: 'deleted' },
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
    });
    expect(await useSourcesStore.getState().annotateSource(SOURCE.id, { kind: 'terms', redistribution: 'permits', reason: 'read' })).toBe(true);
    expect(await useSourcesStore.getState().overrideDetection(SOURCE.id, { text_columns: ['text'] }, 'r')).toBe(true);
    expect(await useSourcesStore.getState().deleteSource(SOURCE.id, 'cleanup')).toBe(true);
    const posts = calls.filter((c) => c.method === 'POST').map((c) => c.body);
    expect(posts).toEqual([{ kind: 'terms', redistribution: 'permits', reason: 'read' }, { kind: 'detection_override', value: { text_columns: ['text'] }, reason: 'r' }]);
    expect(calls.find((c) => c.method === 'DELETE')?.body).toEqual({ reason: 'cleanup' });
    expect(useSourcesStore.getState().selected).toBeNull();
  });

  it('calls only /api/v1/sources/* and the job record, never Hugging Face', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/sources/meta': {},
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      'POST /api/v1/sources/hf/preview': PREVIEW,
      'POST /api/v1/sources/hf': { job_id: 'job_2', source_id: null, existing_job: false },
      'GET /api/v1/jobs/job_2': job({ id: 'job_2', status: 'running' }),
    });
    const s = useSourcesStore.getState();
    await s.fetchSourcesMeta();
    await s.previewHf({ repo_id: 'org/data' }, TOKEN);
    await s.importHf({ repo_id: 'org/data' }, TOKEN);
    await useSourcesStore.getState().pollImports();
    expect(calls.length).toBeGreaterThan(3);
    for (const c of calls) expect(c.path).toMatch(/^\/api\/v1\/(sources|jobs)(\/|\?|$)/);
  });
});

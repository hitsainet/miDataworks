// Feature 003's store against the real API client and a fetch stub (FTASKS 11.1).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { operatorsApi } from '@/api/operators';
import { mockFetch } from '@/test/fetchMock';
import { ALLOWLIST, DROP_SHORT, LIST, PREVIEW, STATS } from '@/test/fixtures003';

import { requestKey, useOperatorsStore } from './operatorsStore';

const reset = () =>
  useOperatorsStore.setState({ catalogue: [], filters: {}, previews: {}, statistics: {}, fieldErrors: [], selected: null, subset: null, allowlist: [], catalogueError: null });

describe('operatorsStore', () => {
  beforeEach(reset);
  afterEach(() => vi.unstubAllGlobals());

  it('fetches the catalogue with filters and keeps the summary', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/operators': LIST });
    await useOperatorsStore.getState().setFilter('kind', 'filter');
    expect(useOperatorsStore.getState().catalogue).toHaveLength(3);
    expect(useOperatorsStore.getState().summary.not_allowed).toBe(1);
    expect(calls[0].path).toContain('kind=filter');
  });

  it('reports a catalogue failure as an error, not an empty list', async () => {
    mockFetch({ 'GET /api/v1/operators': () => ({ status: 500, json: { error: { code: 'X', message: 'boom', details: {} } } }) });
    await useOperatorsStore.getState().fetchCatalogue();
    expect(useOperatorsStore.getState().catalogueError).toBe('boom');
  });

  it('maps a 422 params_invalid into field errors by pointer', async () => {
    mockFetch({
      'POST /api/v1/operators/fx_drop_short/1/validate': () => ({
        status: 422,
        json: { error: { code: 'params_invalid', message: 'bad', details: { errors: [{ pointer: '/min_len', message: "'abc' is not of type 'integer'" }] } } },
      }),
    });
    expect(await useOperatorsStore.getState().validateParams('fx_drop_short', '1', { min_len: 'abc' })).toBe(false);
    expect(useOperatorsStore.getState().fieldErrors).toEqual([{ pointer: '/min_len', message: "'abc' is not of type 'integer'" }]);
  });

  it('selects an operator and keeps per-key preview state', async () => {
    mockFetch({
      'GET /api/v1/operators/fx_drop_short/1': DROP_SHORT,
      'POST /api/v1/operators/fx_drop_short/1/preview': { status: 'done', preview_id: 'p', result: PREVIEW },
    });
    await useOperatorsStore.getState().selectOperator('fx_drop_short', '1');
    expect(useOperatorsStore.getState().selected?.manifest?.thresholds).toHaveLength(1);
    const request = { params: { min_len: 10 }, input: { version_id: 'v1' } };
    const key = await useOperatorsStore.getState().runPreview('fx_drop_short', '1', request);
    expect(key).toBe(requestKey('fx_drop_short', '1', request));
    expect(useOperatorsStore.getState().previews[key].data?.counts.dropped).toBe(2);
  });

  it('a 202 preview is polled until done', async () => {
    let polls = 0;
    mockFetch({
      'POST /api/v1/operators/fx_drop_short/1/preview': () => ({ status: 202, json: { status: 'running', preview_id: 'p1' } }),
      'GET /api/v1/operators/previews/p1': () => (++polls < 2 ? { json: { status: 'running', preview_id: 'p1' } } : { json: { status: 'done', preview_id: 'p1', result: PREVIEW } }),
    });
    const result = await operatorsApi.preview('fx_drop_short', '1', { params: {}, input: { version_id: 'v' } }, async () => undefined);
    expect(result.counts.kept).toBe(8);
    expect(polls).toBe(2);
  });

  it('statistics are fetched once per request and cached', async () => {
    const { calls } = mockFetch({ 'POST /api/v1/operators/fx_drop_short/1/statistics': { status: 'done', preview_id: 's', result: STATS } });
    const request = { params: { min_len: 10 }, input: { version_id: 'v1' } };
    await useOperatorsStore.getState().fetchStatistics('fx_drop_short', '1', request);
    await useOperatorsStore.getState().fetchStatistics('fx_drop_short', '1', request);
    expect(calls.filter((c) => c.path.endsWith('/statistics'))).toHaveLength(1);
  });

  it('allowlist changes send the reason and refresh both lists', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/operators/allowlist': { state: 'allowed' },
      'GET /api/v1/operators/allowlist': ALLOWLIST,
      'GET /api/v1/operators': LIST,
    });
    const change = { distribution: 'acme-ops', distribution_version: '1.0', entry_point: 'tagger', reason: 'reviewed' };
    expect(await useOperatorsStore.getState().changeAllowlist('allow', change)).toBe(true);
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual(change);
    expect(calls.filter((c) => c.method === 'GET').map((c) => c.path.split('?')[0]).sort()).toEqual(['/api/v1/operators', '/api/v1/operators/allowlist']);
  });

  it('an agent refusal surfaces as the allowlist error', async () => {
    mockFetch({ 'POST /api/v1/operators/allowlist/revoke': () => ({ status: 403, json: { error: { code: 'agent_forbidden', message: 'Agents can read but not change.', details: {} } } }) });
    const ok = await useOperatorsStore.getState().changeAllowlist('revoke', { distribution: 'a', distribution_version: '1', entry_point: 'e', reason: 'r' });
    expect(ok).toBe(false);
    expect(useOperatorsStore.getState().allowlistError).toBe('Agents can read but not change.');
  });
});

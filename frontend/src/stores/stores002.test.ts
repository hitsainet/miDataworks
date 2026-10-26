// Feature 002's stores (tasks 14.2, 15.2, 17.2): export bytes unchanged, validation details kept,
// build progress from events and from polling, and the debounced, storage-safe draft.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { job, mockFetch } from '@/test/fetchMock';

import { DRAFT_KEY, SAVE_DEBOUNCE_MS, useDraftsStore } from './draftsStore';
import * as recipes from './recipesStore';
import { useVersionsStore } from './versionsStore';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('recipesStore', () => {
  it('downloads the export response bytes unchanged', async () => {
    const bytes = '{"body":{"format":"dw.recipe/v1","steps":[]},"format":"dw.recipe-file/v1"}';
    vi.stubGlobal('fetch', vi.fn(async () => new Response(bytes, { status: 200 })));
    const saved: Blob[] = [];
    vi.stubGlobal('URL', { ...URL, createObjectURL: (b: Blob) => { saved.push(b); return 'blob:x'; }, revokeObjectURL: () => undefined });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const stringify = vi.spyOn(JSON, 'stringify');
    await recipes.useRecipesStore.getState().exportRecipe('rec-1', 'rev-1', 'humor');
    expect(await saved[0].text()).toBe(bytes);
    expect(stringify).not.toHaveBeenCalled();
  });

  it('the export path never re-serialises: no JSON.stringify in the api or the action', () => {
    const api = readFileSync(join(__dirname, '../api/recipes.ts'), 'utf8');
    const exportFn = api.slice(api.indexOf('exportBlob'), api.indexOf('importFile'));
    expect(exportFn).toContain('res.blob()');
    expect(exportFn).not.toMatch(/JSON\.stringify|res\.json\(/);
  });

  it('keeps the per-step validation the backend sent with recipe_invalid', async () => {
    const details = { valid: false, body_errors: [], steps: [{ index: 1, operator: 'x', version: '1', errors: [{ code: 'operator_not_found', message: 'x 1 is not installed' }] }] };
    mockFetch({ 'POST /api/v1/recipes': () => ({ status: 422, json: { error: { code: 'recipe_invalid', message: 'not runnable', details } } }) });
    const result = await recipes.useRecipesStore.getState().createRecipe('x', { format: 'dw.recipe/v1', steps: [] });
    expect(result).toBeNull();
    expect(recipes.useRecipesStore.getState().validation).toEqual(details);
  });
});

describe('versionsStore build progress', () => {
  beforeEach(() => useVersionsStore.setState({ build: null }));

  it('applies room events for its own job only', () => {
    const { trackBuild, applyBuildEvent } = useVersionsStore.getState();
    trackBuild('job_1');
    applyBuildEvent('version_build:progress', { job_id: 'job_2', phase: 'ingest', percent: 50 });
    expect(useVersionsStore.getState().build?.phase).toBeNull();
    applyBuildEvent('version_build:progress', { job_id: 'job_1', phase: 'ingest', percent: 50 });
    expect(useVersionsStore.getState().build).toMatchObject({ phase: 'ingest', percent: 50, status: 'running' });
    applyBuildEvent('version_build:completed', { job_id: 'job_1', version_id: 'v-9' });
    expect(useVersionsStore.getState().build).toMatchObject({ status: 'completed', versionId: 'v-9' });
  });

  it('falls back to the job record when the socket is down', async () => {
    mockFetch({ 'GET /api/v1/jobs/job_1': job({ id: 'job_1', status: 'failed', progress: 30, result: { error: { message: 'Step 1 dropped rows with no reason.' } } }) });
    useVersionsStore.getState().trackBuild('job_1');
    await useVersionsStore.getState().pollBuild();
    expect(useVersionsStore.getState().build).toMatchObject({ status: 'failed', error: 'Step 1 dropped rows with no reason.' });
  });
});

describe('draftsStore', () => {
  const draft = { id: 'draft-1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'import' }, updated_by: 'Ada', updated_at: '' };

  it('saves once after the debounce, not on every keystroke', async () => {
    vi.useFakeTimers();
    const { calls } = mockFetch({ 'PUT /api/v1/recipe-drafts/draft-1': draft });
    useDraftsStore.setState({ draft });
    const { updateDraft } = useDraftsStore.getState();
    updateDraft({ name: 'a' });
    updateDraft({ name: 'ab' });
    updateDraft({ name: 'abc' });
    expect(calls).toHaveLength(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 10); });
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ name: 'abc' });
    expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? '{}').id).toBe('draft-1');
  });

  it('resumes the draft remembered locally after a reload', async () => {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ id: 'draft-1' }));
    const { calls } = mockFetch({ 'GET /api/v1/recipe-drafts/draft-1': { ...draft, flow_state: { step: 'label' } } });
    const loaded = await useDraftsStore.getState().loadDraft();
    expect(loaded?.flow_state.step).toBe('label');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('works when storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    mockFetch({ 'POST /api/v1/recipe-drafts': draft });
    const loaded = await useDraftsStore.getState().loadDraft();
    expect(loaded?.id).toBe('draft-1');
  });
});

// Rubric authoring on the Label runs screen and in the Label step: the real panels, store and API
// client against a fetch stub. Each test clicks through from the screen, so removing the Rubrics
// button, the library's mount, or the form's create, clone, import or export call turns it red.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RubricPicker } from '@/components/settings/RubricPicker';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { getPanel } from '@/config/panels';
import { useLabelingStore } from '@/stores/labelingStore';
import { mockFetch } from '@/test/fetchMock';
import { TEMPLATE } from '@/test/fixtures005';
import type { Rubric } from '@/types/labeling';

import { LabelStep } from './LabelStep';

const BODY = {
  style: 'pointwise',
  messages: [
    { role: 'system', content: 'You judge humour.' },
    { role: 'user', content: 'Text: {text}\n{question}\nAnswer yes or no.' },
  ],
  input_fields: ['text'],
  axes: [],
  parser: 'verdict_line_v1',
  allowed_verdicts: ['yes', 'no'],
  json_schema: null,
  pair_fields: null,
  swap_map: null,
};

const RUBRIC: Rubric = {
  id: 'rb_1', name: 'humor/judge', version: 1, ref: 'humor/judge@1', content_hash: 'b'.repeat(64), style: 'pointwise',
  body: BODY, used: false, created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-07T00:00:00Z',
};
const saved = (over: Partial<Rubric>): Rubric => ({ ...RUBRIC, id: 'rb_new', ...over });

function routes(extra: Record<string, unknown> = {}) {
  return mockFetch({
    'GET /api/v1/label-runs': { items: [], total: 0, page: 1, limit: 50 },
    'GET /api/v1/rubrics': [RUBRIC],
    ...extra,
  });
}

const blobText = (b: Blob) =>
  new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(b);
  });

const posts = (calls: Array<{ method: string; path: string; body: unknown }>, path: string) =>
  calls.filter((c) => c.method === 'POST' && c.path === path);

async function openLibrary() {
  render(<LabelRunsPanel panel={getPanel('label-runs')} />);
  fireEvent.click(screen.getByRole('button', { name: 'Rubrics' }));
  await screen.findByText('humor/judge@1');
}

beforeEach(() => useLabelingStore.setState({ rubrics: [], rubricError: null, error: null }));
afterEach(() => vi.unstubAllGlobals());

describe('New rubric', () => {
  it('sends a pointwise rubric once with every field', async () => {
    const { calls } = routes({ 'POST /api/v1/rubrics': () => ({ status: 201, json: saved({ name: 'tone', ref: 'tone@1' }) }) });
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'tone' } });
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'You rate tone.' } });
    fireEvent.change(screen.getByLabelText('Message 2 content'), { target: { value: '{text}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add message' }));
    fireEvent.change(screen.getByLabelText('Message 3 role'), { target: { value: 'assistant' } });
    fireEvent.change(screen.getByLabelText('Message 3 content'), { target: { value: 'Verdict:' } });
    fireEvent.change(screen.getByLabelText('Input fields'), { target: { value: 'text, context' } });
    fireEvent.change(screen.getByLabelText('Axes'), { target: { value: 'warmth' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'warm, cold' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));

    expect(await screen.findByTestId('rubric-notice')).toHaveTextContent('Saved tone@1 (pointwise).');
    const sent = posts(calls, '/api/v1/rubrics');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({
      name: 'tone',
      body: {
        style: 'pointwise',
        messages: [
          { role: 'system', content: 'You rate tone.' },
          { role: 'user', content: '{text}' },
          { role: 'assistant', content: 'Verdict:' },
        ],
        input_fields: ['text', 'context'],
        axes: ['warmth'],
        parser: 'verdict_line_v1',
        allowed_verdicts: ['warm', 'cold'],
      },
    });
    expect(useLabelingStore.getState().rubrics.map((r) => r.ref)).toContain('tone@1');
  });

  it('sends pair fields and the swap map for a pairwise rubric, and a schema for json_v1', async () => {
    const { calls } = routes({ 'POST /api/v1/rubrics': () => ({ status: 201, json: saved({ name: 'better', ref: 'better@1', style: 'pairwise' }) }) });
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'better' } });
    fireEvent.change(screen.getByLabelText('Rubric style'), { target: { value: 'pairwise' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove message 1' }));
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'A: {a}\nB: {b}' } });
    fireEvent.change(screen.getByLabelText('Input fields'), { target: { value: 'prompt' } });
    fireEvent.change(screen.getByLabelText('Answer format'), { target: { value: 'json_v1' } });
    fireEvent.change(screen.getByLabelText('Rubric JSON schema'), { target: { value: '{"type":"object","required":["verdict"]}' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'A, B, tie' } });
    fireEvent.change(screen.getByLabelText('Pair field A'), { target: { value: 'chosen' } });
    fireEvent.change(screen.getByLabelText('Pair field B'), { target: { value: 'rejected' } });
    fireEvent.change(screen.getByLabelText('Swap map'), { target: { value: 'A=B, B=A, tie=tie' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));

    await screen.findByTestId('rubric-notice');
    const sent = posts(calls, '/api/v1/rubrics');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({
      name: 'better',
      body: {
        style: 'pairwise',
        messages: [{ role: 'user', content: 'A: {a}\nB: {b}' }],
        input_fields: ['prompt'],
        parser: 'json_v1',
        allowed_verdicts: ['A', 'B', 'tie'],
        json_schema: { type: 'object', required: ['verdict'] },
        pair_fields: ['chosen', 'rejected'],
        swap_map: { A: 'B', B: 'A', tie: 'tie' },
      },
    });
  });

  it('refuses a JSON schema that is not JSON without calling the API', async () => {
    const { calls } = routes();
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'j' } });
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Message 2 content'), { target: { value: '{text}' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'yes' } });
    fireEvent.change(screen.getByLabelText('Answer format'), { target: { value: 'json_v1' } });
    fireEvent.change(screen.getByLabelText('Rubric JSON schema'), { target: { value: '{type: "object"}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));
    expect(await screen.findByTestId('rubric-refusal')).toHaveTextContent('The JSON schema is not valid JSON');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('shows the backend refusal verbatim, with each field problem', async () => {
    routes({
      'POST /api/v1/rubrics': () => ({
        status: 422,
        json: { error: { code: 'VALIDATION_ERROR', message: 'The request is not valid.', details: { errors: [{ loc: ['body', 'body'], msg: 'Value error, swap_map keys must be allowed verdicts', type: 'value_error' }] } } },
      }),
    });
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'p' } });
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Message 2 content'), { target: { value: '{text}' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'yes' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));
    const box = await screen.findByTestId('rubric-refusal');
    expect(box).toHaveTextContent('The request is not valid.');
    expect(box).toHaveTextContent('body: Value error, swap_map keys must be allowed verdicts');
  });
});

describe('Clone, import and export', () => {
  it('clones with the body prefilled and edited, once', async () => {
    const { calls } = routes({ 'POST /api/v1/rubrics/rb_1/clone': () => ({ status: 201, json: saved({ version: 2, ref: 'humor/judge@2' }) }) });
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'Clone humor/judge@1' }));
    expect(screen.getByLabelText('Rubric name')).toBeDisabled();
    expect((screen.getByLabelText('Message 1 content') as HTMLTextAreaElement).value).toBe('You judge humour.');
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'yes, no, unsure' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save the clone' }));
    expect(await screen.findByTestId('rubric-notice')).toHaveTextContent('Saved humor/judge@2 (pointwise).');
    const sent = posts(calls, '/api/v1/rubrics/rb_1/clone');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({ body: { ...BODY, axes: undefined, json_schema: undefined, pair_fields: undefined, swap_map: undefined, allowed_verdicts: ['yes', 'no', 'unsure'] } });
    expect(posts(calls, '/api/v1/rubrics')).toHaveLength(0);
  });

  it('imports a midataworks.rubric/v1 file as read, once', async () => {
    const doc = { format: 'midataworks.rubric/v1', name: 'humor/judge', version: 3, body: BODY };
    const { calls } = routes({ 'POST /api/v1/rubrics/import': () => ({ status: 201, json: saved({ version: 3, ref: 'humor/judge@3' }) }) });
    await openLibrary();
    const file = new File([JSON.stringify(doc)], 'humor.rubric.json', { type: 'application/json' });
    fireEvent.change(screen.getByLabelText('Rubric file to import'), { target: { files: [file] } });
    expect(await screen.findByTestId('rubric-notice')).toHaveTextContent('Imported humor/judge@3 (pointwise).');
    const sent = posts(calls, '/api/v1/rubrics/import');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual(doc);
  });

  it('refuses a file that is not JSON without calling the API', async () => {
    const { calls } = routes();
    await openLibrary();
    fireEvent.change(screen.getByLabelText('Rubric file to import'), { target: { files: [new File(['not json'], 'bad.json')] } });
    expect(await screen.findByTestId('rubric-library-refusal')).toHaveTextContent('bad.json is not JSON');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('shows an import refusal verbatim', async () => {
    routes({
      'POST /api/v1/rubrics/import': () => ({
        status: 409,
        json: { error: { code: 'RUBRIC_VERSION_EXISTS', message: 'humor/judge@1 already exists with a different body. Clone it to make a new version.', details: {} } },
      }),
    });
    await openLibrary();
    fireEvent.change(screen.getByLabelText('Rubric file to import'), { target: { files: [new File([JSON.stringify({ format: 'midataworks.rubric/v1', name: 'humor/judge', version: 1, body: BODY })], 'r.json')] } });
    expect(await screen.findByTestId('rubric-library-refusal')).toHaveTextContent('humor/judge@1 already exists with a different body. Clone it to make a new version.');
  });

  it('exports the document as a downloaded file', async () => {
    const doc = { format: 'midataworks.rubric/v1', name: 'humor/judge', version: 1, body: BODY };
    const { calls } = routes({ 'GET /api/v1/rubrics/rb_1/export': doc });
    const blobs: Blob[] = [];
    vi.stubGlobal('URL', { ...URL, createObjectURL: (b: Blob) => { blobs.push(b); return 'blob:x'; }, revokeObjectURL: () => undefined });
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download); });
    await openLibrary();
    fireEvent.click(screen.getByRole('button', { name: 'Export humor/judge@1' }));
    await waitFor(() => expect(blobs).toHaveLength(1));
    expect(calls.filter((c) => c.path === '/api/v1/rubrics/rb_1/export')).toHaveLength(1);
    expect(JSON.parse(await blobText(blobs[0]))).toEqual(doc);
    expect(names).toEqual(['humor_judge@1.rubric.json']);
    click.mockRestore();
  });
});

describe('the Label step', () => {
  it('opens the library under the rubric choice and selects the rubric just saved', async () => {
    routes({
      'GET /api/v1/decision-templates': [TEMPLATE],
      'GET /api/v1/versions': { items: [], total: 0 },
      'POST /api/v1/rubrics': () => ({ status: 201, json: saved({ name: 'tone', ref: 'tone@1' }) }),
    });
    render(<LabelStep />);
    fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'judge' } });
    fireEvent.click(screen.getByRole('button', { name: 'Write, import or clone a rubric' }));
    await screen.findByTestId('rubric-library');
    fireEvent.click(screen.getByRole('button', { name: 'New rubric' }));
    fireEvent.change(screen.getByLabelText('Rubric name'), { target: { value: 'tone' } });
    fireEvent.change(screen.getByLabelText('Message 1 content'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Message 2 content'), { target: { value: '{text}' } });
    fireEvent.change(screen.getByLabelText('Allowed verdicts'), { target: { value: 'yes' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save rubric' }));
    await waitFor(() => expect(screen.getByLabelText('Rubric')).toHaveValue('rb_new'));
  });
});

describe('Settings', () => {
  it('points at the Label runs screen when there are no rubrics', async () => {
    mockFetch({ 'GET /api/v1/rubrics': [] });
    render(<RubricPicker />);
    expect(await screen.findByText('No rubrics yet. Write or import one on Label runs, under Rubrics.')).toBeInTheDocument();
  });
});

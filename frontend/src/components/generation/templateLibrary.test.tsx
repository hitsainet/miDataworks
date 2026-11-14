// Template authoring on the Generation screen: the real panel, store and API client against a
// fetch stub. Each test clicks through from the panel, so removing the panel's Templates pane, the
// form's create or clone call, or the saved-placeholders notice turns it red.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { getPanel } from '@/config/panels';
import { useGenerationStore } from '@/stores/generationStore';
import { mockFetch } from '@/test/fetchMock';
import type { GenerationTemplate } from '@/types/generation';

const MINIMAL: GenerationTemplate = {
  id: 'gt_min', name: 'minimal-pair-v1', version: 1, ref: 'minimal-pair-v1@1', kind: 'respond',
  description: 'Minimal pairs (009): one minimal edit that flips the concept. Clone it and name your concept in the prompt.',
  body: {
    prompt: 'Edit the text below so the concept is flipped.\n\nText:\n{prompt}',
    system: 'You make minimal edits to text.',
    sampling: { temperature: 0.2, top_p: 1.0, max_tokens: 512 },
    structured_output: 'none',
  },
  content_hash: 'h1', builtin: true, used: false, placeholders: ['prompt'],
};

const saved = (over: Partial<GenerationTemplate>): GenerationTemplate => ({ ...MINIMAL, builtin: false, id: 'gt_new', ...over });

function routes(extra: Record<string, unknown> = {}) {
  return mockFetch({
    'GET /api/v1/generation-runs': { items: [], total: 0, page: 1, limit: 50 },
    'GET /api/v1/generation-templates': { items: [MINIMAL], total: 1, page: 1, limit: 50 },
    ...extra,
  });
}

const posts = (calls: Array<{ method: string; path: string; body: unknown }>, path: string) =>
  calls.filter((c) => c.method === 'POST' && c.path === path);

async function openTemplates() {
  render(<GenerationPanel panel={getPanel('generation')} />);
  fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
  await screen.findByText('minimal-pair-v1@1');
}

beforeEach(() => useGenerationStore.setState({ templates: [], templateError: null, list: [], byId: {}, error: null }));
afterEach(() => vi.unstubAllGlobals());

describe('New template', () => {
  it('sends the whole body once and shows the ref and the placeholders the backend found', async () => {
    const { calls } = routes({
      'POST /api/v1/generation-templates': () => ({ status: 201, json: saved({ name: 'topic-respond', ref: 'topic-respond@1', placeholders: ['prompt', 'topic'] }) }),
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'topic-respond' } });
    fireEvent.change(screen.getByLabelText('Template kind'), { target: { value: 'respond' } });
    fireEvent.change(screen.getByLabelText('Template description'), { target: { value: 'Answers about a topic.' } });
    fireEvent.change(screen.getByLabelText('System message'), { target: { value: 'Be brief.' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'About {topic}: {prompt}' } });
    fireEvent.change(screen.getByLabelText('Temperature'), { target: { value: '0.3' } });
    fireEvent.change(screen.getByLabelText('Top-p'), { target: { value: '0.9' } });
    fireEvent.change(screen.getByLabelText('Max tokens'), { target: { value: '256' } });
    fireEvent.change(screen.getByLabelText('Structured output'), { target: { value: 'json_schema' } });
    fireEvent.change(screen.getByLabelText('JSON schema'), { target: { value: '{"type": "object"}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save template' }));

    const notice = await screen.findByTestId('saved-template');
    expect(notice).toHaveTextContent('Saved as topic-respond@1.');
    expect(within(screen.getByTestId('saved-placeholders')).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['{prompt}', '{topic}']);
    const sent = posts(calls, '/api/v1/generation-templates');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({
      name: 'topic-respond',
      kind: 'respond',
      description: 'Answers about a topic.',
      body: {
        prompt: 'About {topic}: {prompt}',
        system: 'Be brief.',
        sampling: { temperature: 0.3, top_p: 0.9, max_tokens: 256 },
        structured_output: 'json_schema',
        json_schema: { type: 'object' },
      },
    });
    // The saved template joins the list the run forms choose from.
    expect(useGenerationStore.getState().templates.map((t) => t.ref)).toContain('topic-respond@1');
  });

  it('says so when the backend found no placeholders', async () => {
    routes({ 'POST /api/v1/generation-templates': () => ({ status: 201, json: saved({ name: 'fixed', ref: 'fixed@1', placeholders: [] }) }) });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'fixed' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Say hello.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save template' }));
    expect(await screen.findByTestId('saved-placeholders')).toHaveTextContent('None. Every request sends the same text.');
  });

  it('refuses a JSON schema that is not JSON without calling the API', async () => {
    const { calls } = routes();
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'structured' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: '{prompt}' } });
    fireEvent.change(screen.getByLabelText('Structured output'), { target: { value: 'json_schema' } });
    fireEvent.change(screen.getByLabelText('JSON schema'), { target: { value: '{"type": object}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save template' }));
    expect(await screen.findByTestId('template-refusal')).toHaveTextContent('The JSON schema is not valid JSON');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });

  it('shows the backend refusal verbatim, with each field problem', async () => {
    routes({
      'POST /api/v1/generation-templates': () => ({
        status: 422,
        json: { error: { code: 'VALIDATION_ERROR', message: 'The request is not valid.', details: { errors: [{ loc: ['body', 'body', 'sampling', 'top_p'], msg: 'Input should be less than or equal to 1', type: 'less_than_equal' }] } } },
      }),
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: '{prompt}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save template' }));
    const box = await screen.findByTestId('template-refusal');
    expect(box).toHaveTextContent('The request is not valid.');
    expect(box).toHaveTextContent('body.sampling.top_p: Input should be less than or equal to 1');
  });

  it('shows a name conflict as the backend words it', async () => {
    routes({
      'POST /api/v1/generation-templates': () => ({
        status: 409,
        json: { error: { code: 'TEMPLATE_NAME_EXISTS', message: "A template named 'minimal-pair-v1' exists; clone it to make version 2.", details: {} } },
      }),
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'minimal-pair-v1' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: '{prompt}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save template' }));
    expect(await screen.findByTestId('template-refusal')).toHaveTextContent("A template named 'minimal-pair-v1' exists; clone it to make version 2.");
    expect(screen.queryByTestId('saved-template')).toBeNull();
  });

  it('keeps Save disabled for a name the backend would refuse', async () => {
    routes();
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Template name'), { target: { value: 'Has Spaces' } });
    expect(screen.getByRole('button', { name: 'Save template' })).toBeDisabled();
  });
});

describe('Clone', () => {
  it('prefills from a built-in template and sends the edited body and description once', async () => {
    const { calls } = routes({
      'POST /api/v1/generation-templates/gt_min/clone': () => ({ status: 201, json: saved({ id: 'gt_min2', version: 2, ref: 'minimal-pair-v1@2', placeholders: ['prompt'] }) }),
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'Clone minimal-pair-v1@1' }));
    const prompt = screen.getByLabelText('Prompt') as HTMLTextAreaElement;
    expect(prompt.value).toBe(MINIMAL.body.prompt);
    expect((screen.getByLabelText('System message') as HTMLTextAreaElement).value).toBe('You make minimal edits to text.');
    expect((screen.getByLabelText('Temperature') as HTMLInputElement).value).toBe('0.2');
    expect(screen.getByLabelText('Template name')).toBeDisabled();
    fireEvent.change(prompt, { target: { value: 'Flip the concept "formality" in:\n{prompt}' } });
    fireEvent.change(screen.getByLabelText('Template description'), { target: { value: 'Formality pairs.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save the clone' }));

    expect(await screen.findByTestId('saved-template')).toHaveTextContent('Saved as minimal-pair-v1@2.');
    const sent = posts(calls, '/api/v1/generation-templates/gt_min/clone');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toEqual({
      description: 'Formality pairs.',
      body: {
        prompt: 'Flip the concept "formality" in:\n{prompt}',
        system: 'You make minimal edits to text.',
        sampling: { temperature: 0.2, top_p: 1, max_tokens: 512 },
        structured_output: 'none',
      },
    });
    expect(posts(calls, '/api/v1/generation-templates')).toHaveLength(0);
  });

  it('shows a clone refusal verbatim', async () => {
    routes({
      'POST /api/v1/generation-templates/gt_min/clone': () => ({
        status: 409,
        json: { error: { code: 'TEMPLATE_VERSION_EXISTS', message: 'minimal-pair-v1@2 already exists with a different body. Clone it to make a new version.', details: {} } },
      }),
    });
    await openTemplates();
    fireEvent.click(screen.getByRole('button', { name: 'Clone minimal-pair-v1@1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save the clone' }));
    await waitFor(() => expect(screen.getByTestId('template-refusal')).toHaveTextContent('minimal-pair-v1@2 already exists with a different body.'));
  });
});

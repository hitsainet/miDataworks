import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getPanel } from '@/config/panels';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { useHealthStore } from '@/stores/healthStore';

import { ONE_MODEL_CALLOUT, SettingsPanel } from './SettingsPanel';

const role = (r: string, extra: Record<string, unknown> = {}) => ({
  role: r, configured: false, protocol: null, base_url: null, model_id: null, api_key: null,
  has_api_key: false, inherit_from_judge: r === 'generation' || r === 'embeddings', use_mode: 'own', effective_role: null, ...extra,
});
const ROLES = [
  role('classifier', { protocol: 'openai_scoring', base_url: 'http://millm.test/v1', api_key: 'sk-...cdef', has_api_key: true }),
  role('judge'),
  role('generation'),
  role('embeddings'),
];
const SETTINGS = [
  { key: 'hf_token', value: 'hf_...6789', is_sensitive: true, is_set: true, category: 'api_keys', description: '', type: 'secret' },
  { key: 'operator_name', value: 'Ada', is_sensitive: false, is_set: true, category: 'identity', description: '', type: 'string' },
];

describe('SettingsPanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  const setup = (extra = {}) => {
    useHealthStore.setState({ health: HEALTH as never });
    const mock = mockFetch({ 'GET /api/v1/settings': SETTINGS, 'GET /api/v1/endpoint-roles': ROLES, ...extra });
    render(<SettingsPanel panel={getPanel('settings')} />);
    return mock;
  };

  it('shows the four roles, the judge Use setting and the one-model callout', async () => {
    setup();
    for (const r of ['classifier', 'judge', 'generation', 'embeddings']) {
      expect(await screen.findByTestId(`role-${r}`)).toBeInTheDocument();
    }
    expect(within(screen.getByTestId('role-judge')).getByLabelText('Use')).toBeInTheDocument();
    expect(screen.getByText(ONE_MODEL_CALLOUT)).toBeInTheDocument();
    expect(within(screen.getByTestId('role-generation')).getByLabelText('Use the judge endpoint')).toBeChecked();
  });

  it('saving a role without typing a key sends api_key null, never the mask', async () => {
    const { calls } = setup({ 'PUT /api/v1/endpoint-roles/classifier': ROLES[0] });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Save the classifier endpoint' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts).toEqual([
      {
        method: 'PUT',
        path: '/api/v1/endpoint-roles/classifier',
        body: { protocol: 'openai_scoring', base_url: 'http://millm.test/v1', model_id: null, api_key: null, inherit_from_judge: false, use_mode: 'own' },
      },
    ]);
  });

  it('Fetch models asks for the URL in the field and offers the models', async () => {
    const { calls } = setup({
      'GET /api/v1/endpoint-roles/classifier/models': { role: 'classifier', models: ['jev-9b', 'deberta'], source: 'openai', url: 'http://millm.test/v1/models' },
    });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Fetch models' }));
    expect(await within(card).findByRole('option', { name: 'jev-9b' })).toBeInTheDocument();
    expect(calls.filter((c) => c.path.includes('/models'))).toEqual([
      { method: 'GET', path: '/api/v1/endpoint-roles/classifier/models?base_url=http%3A%2F%2Fmillm.test%2Fv1', body: undefined },
    ]);
  });

  it('a failed Fetch models shows the server reason', async () => {
    setup({
      'GET /api/v1/endpoint-roles/classifier/models': () => ({ status: 502, json: { error: { code: 'ENDPOINT_NOT_JSON', message: 'The address may point at a web page.', details: {} } } }),
    });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Fetch models' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('web page');
  });

  it('saves the operator name', async () => {
    const { calls } = setup({ 'PUT /api/v1/settings/operator_name': SETTINGS[1] });
    const input = await screen.findByLabelText('Name');
    await waitFor(() => expect(input).toHaveValue('Ada'));
    fireEvent.change(input, { target: { value: 'Grace' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save your name' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([
      { method: 'PUT', path: '/api/v1/settings/operator_name', body: { value: 'Grace' } },
    ]);
  });

  it('never shows a token in clear: the field placeholder is the masked value', async () => {
    setup();
    const token = await screen.findByLabelText('Hugging Face token');
    await waitFor(() => expect(token).toHaveAttribute('placeholder', 'hf_...6789'));
    expect(token).toHaveValue('');
  });
});

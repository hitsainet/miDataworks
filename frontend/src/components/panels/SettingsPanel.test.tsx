import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getPanel } from '@/config/panels';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { useHealthStore } from '@/stores/healthStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { META } from '@/test/fixtures001';

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

const AGENT_ACCESS = {
  mcp_public_url: null,
  mcp: { reachable: false, categories: null, reason: 'MCP_INTERNAL_URL is not set' },
  gated_actions: {},
  label_threshold: 5000,
  label_window_hours: 24,
  approval_ttl_hours: 24,
  activity: { window: 'last hour', identities: [], sessions: 0, requests: 0 },
};

describe('SettingsPanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  const setup = (extra = {}) => {
    useHealthStore.setState({ health: HEALTH as never });
    const mock = mockFetch({
      'GET /api/v1/settings': SETTINGS,
      'GET /api/v1/endpoint-roles': ROLES,
      'GET /api/v1/agent-access': AGENT_ACCESS,
      // feature 004's global warning level card (ShortcutLevelControl)
      'GET /api/v1/settings/shortcut-level': { margin_pp: 10, source: 'code_default', level: { margin_pp: 10, source: 'code_default', set_by: null, set_at: null, reason: null }, history: [] },
      ...extra,
    });
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

  it('renders the Agent access card from GET /api/v1/agent-access (010 task 11.5)', async () => {
    const { calls } = setup({
      'GET /api/v1/agent-access': {
        mcp_public_url: 'http://mcp-dataworks.hitsai.local/mcp',
        mcp: { reachable: true, categories: ['core'], reason: null },
        gated_actions: { hub_push: 'x' },
        label_threshold: 5000,
        label_window_hours: 24,
        approval_ttl_hours: 24,
        activity: { window: 'last hour', identities: [], sessions: 0, requests: 0 },
      },
    });
    const card = await screen.findByTestId('agent-access-card');
    expect(await within(card).findByLabelText('MCP server address')).toHaveValue('http://mcp-dataworks.hitsai.local/mcp');
    expect(calls.filter((c) => c.path === '/api/v1/agent-access')).toHaveLength(1);
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

  it('shows the global shortcut warning level (feature 004)', async () => {
    setup();
    expect(await screen.findByText(/the default \(10 points, P-19\)/)).toBeInTheDocument();
  });

  it('a failed Fetch models shows the server reason', async () => {
    setup({
      'GET /api/v1/endpoint-roles/classifier/models': () => ({ status: 502, json: { error: { code: 'ENDPOINT_NOT_JSON', message: 'The address may point at a web page.', details: {} } } }),
    });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Fetch models' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('web page');
  });

  it('shows the storage limits in force and saves a new upload cap (T-02)', async () => {
    useSourcesStore.setState({ meta: null });
    const { calls } = setup({ 'GET /api/v1/sources/meta': META, 'PUT /api/v1/settings/upload_max_bytes': { ...SETTINGS[1], key: 'upload_max_bytes', value: '1048576' } });
    const limits = await screen.findByTestId('storage-limits');
    await waitFor(() => expect(limits).toHaveTextContent('In force: 2.0 GB (deployment default)'));
    const input = within(limits).getByLabelText('Largest upload, in GB');
    expect(input).toHaveAttribute('step', '1'); // the arrows move 1 GB
    fireEvent.change(input, { target: { value: '3' } });
    expect(limits).toHaveTextContent('Saves 3,221,225,472 bytes.');
    fireEvent.click(within(limits).getAllByRole('button', { name: 'Save the limit' })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/v1/settings/upload_max_bytes', body: { value: '3221225472' } }]);
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/sources/meta')).toHaveLength(2)); // the limit in force is re-read
  });

  it('takes a custom size in GB and refuses a size of 0', async () => {
    useSourcesStore.setState({ meta: null });
    const { calls } = setup({ 'GET /api/v1/sources/meta': META, 'PUT /api/v1/settings/import_confirm_bytes': { ...SETTINGS[1], key: 'import_confirm_bytes', value: '2684354560' } });
    const limits = await screen.findByTestId('storage-limits');
    const input = within(limits).getByLabelText('Imports above this size need a confirmation, in GB');
    const save = within(limits).getAllByRole('button', { name: 'Save the limit' })[1];
    fireEvent.change(input, { target: { value: '0' } });
    expect(limits).toHaveTextContent('Enter a size above 0 GB.');
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: '2.5' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/v1/settings/import_confirm_bytes', body: { value: '2684354560' } }]);
  });

  it('shows a stored limit in GB, and clearing the box returns it to the deployment default', async () => {
    useSourcesStore.setState({ meta: null });
    const { calls } = setup({
      'GET /api/v1/settings': [...SETTINGS, { ...SETTINGS[1], key: 'upload_max_bytes', value: '3072000000', category: 'storage' }],
      'GET /api/v1/sources/meta': META,
      'PUT /api/v1/settings/upload_max_bytes': { ...SETTINGS[1], key: 'upload_max_bytes', value: '' },
    });
    const limits = await screen.findByTestId('storage-limits');
    const input = within(limits).getByLabelText('Largest upload, in GB');
    await waitFor(() => expect(input).toHaveValue(2.861));
    fireEvent.change(input, { target: { value: '' } });
    expect(limits).toHaveTextContent('Saving clears it to the deployment default.');
    fireEvent.click(within(limits).getAllByRole('button', { name: 'Save the limit' })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/v1/settings/upload_max_bytes', body: { value: '' } }]);
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

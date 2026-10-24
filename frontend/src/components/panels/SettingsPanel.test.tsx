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
  role('classifier', { protocol: 'openai_scoring', base_url: 'http://millm.test/v1', api_key: 'sk-...cdef', has_api_key: true, key_origin: 'http://millm.test:80' }),
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
      // the Environment card (feature 012)
      'GET /api/v1/settings/environment': { settings: [], history: [] },
      // the Settings PIN card (feature 014)
      'GET /api/v1/settings/pin': { configured: false, bypass_active: false, session_valid: false, session_ttl_s: 1800, header: 'X-Settings-Pin-Session' },
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

  it('says before saving that the key stays with its server, and which key the new server uses (014)', async () => {
    const kept = { origin: 'https://api.typesafe.test:443', api_key: 'ts-...9876', in_use: false };
    setup({
      'GET /api/v1/endpoint-roles': [
        { ...ROLES[0], stored_keys: [{ origin: 'http://millm.test:80', api_key: 'sk-...cdef', in_use: true }, kept] },
        ...ROLES.slice(1),
      ],
    });
    const card = await screen.findByTestId('role-classifier');
    expect(within(card).getByText(/sent only to http:\/\/millm\.test:80/)).toBeInTheDocument();
    const url = within(card).getByLabelText('Endpoint URL');
    fireEvent.change(url, { target: { value: 'http://millm.test/v2' } });
    expect(within(card).queryByTestId('classifier-key-on-move')).toBeNull();
    fireEvent.change(url, { target: { value: 'https://api.typesafe.test/v1' } });
    expect(within(card).getByTestId('classifier-key-on-move')).toHaveTextContent(
      'The key for http://millm.test:80 is kept for that server and sent nowhere else. Saving uses the key stored earlier for https://api.typesafe.test:443.',
    );
    fireEvent.change(url, { target: { value: 'https://third.test/v1' } });
    expect(within(card).getByTestId('classifier-key-on-move')).toHaveTextContent('No key is stored for https://third.test:443');
    // A new key typed for the new server: nothing to explain.
    fireEvent.change(within(card).getByLabelText('API key'), { target: { value: 'sk-new-key-123456' } });
    expect(within(card).queryByTestId('classifier-key-on-move')).toBeNull();
  });

  it('lists the stored keys per server and deletes one by its origin (014)', async () => {
    const confirm = vi.fn(() => true);
    vi.stubGlobal('confirm', confirm);
    const stored = [
      { origin: 'http://millm.test:80', api_key: 'sk-...cdef', in_use: true },
      { origin: 'https://api.typesafe.test:443', api_key: 'ts-...9876', in_use: false },
    ];
    const { calls } = setup({
      'GET /api/v1/endpoint-roles': [{ ...ROLES[0], stored_keys: stored }, ...ROLES.slice(1)],
      'DELETE /api/v1/endpoint-roles/classifier/keys': { ...ROLES[0], stored_keys: [stored[0]] },
    });
    const list = await screen.findByTestId('classifier-stored-keys');
    expect(within(list).getByText('in use')).toBeInTheDocument();
    expect(within(list).getByText('kept for this server')).toBeInTheDocument();
    fireEvent.click(within(list).getByRole('button', { name: 'Delete the key for https://api.typesafe.test:443' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([
      { method: 'DELETE', path: '/api/v1/endpoint-roles/classifier/keys?origin=https%3A%2F%2Fapi.typesafe.test%3A443', body: undefined },
    ]);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it('shows the server\'s plain message about what a move did to the keys (014)', async () => {
    const notice =
      'The API key for http://millm.test:80 is kept for that server and is sent only there; it comes back if you point this endpoint at it again. The classifier endpoint now uses the key stored earlier for https://api.typesafe.test:443.';
    setup({
      'PUT /api/v1/endpoint-roles/classifier': {
        ...ROLES[0],
        base_url: 'https://api.typesafe.test/v1',
        key_origin: 'https://api.typesafe.test:443',
        key_restored: true,
        key_parked: true,
        key_notice: notice,
      },
    });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.change(within(card).getByLabelText('Endpoint URL'), { target: { value: 'https://api.typesafe.test/v1' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save the classifier endpoint' }));
    expect(await screen.findByTestId('settings-warning')).toHaveTextContent(`Saved the classifier endpoint. ${notice}`);
    expect(screen.queryByText('Saved the classifier endpoint.')).toBeNull();
  });

  it('a save that keeps the key says Saved and shows no warning', async () => {
    setup({ 'PUT /api/v1/endpoint-roles/classifier': { ...ROLES[0], key_notice: null } });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Save the classifier endpoint' }));
    expect(await screen.findByText('Saved the classifier endpoint.')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-warning')).toBeNull();
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

  it('points to the Approvals screen instead of listing agent requests (2026-10-20)', async () => {
    const { calls } = setup();
    const card = await screen.findByTestId('settings-approvals-link');
    expect(screen.queryByTestId('approval-history')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Open Approvals' }));
    expect(window.location.hash).toBe('#/approvals');
    window.location.hash = '';
    expect(calls.filter((c) => c.path.split('?')[0] === '/api/v1/approvals')).toHaveLength(0);
  });

  it('a failed Fetch models shows the server reason', async () => {
    setup({
      'GET /api/v1/endpoint-roles/classifier/models': () => ({ status: 502, json: { error: { code: 'ENDPOINT_NOT_JSON', message: 'The address may point at a web page.', details: {} } } }),
    });
    const card = await screen.findByTestId('role-classifier');
    fireEvent.click(within(card).getByRole('button', { name: 'Fetch models' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('web page');
  });

  it('the Storage card points to the byte limits, which are on the Environment card (feature 012)', async () => {
    setup();
    expect(await screen.findByText('The largest upload and the import confirmation size are under Environment, below.')).toBeInTheDocument();
    expect(await screen.findByTestId('environment-card')).toBeInTheDocument();
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

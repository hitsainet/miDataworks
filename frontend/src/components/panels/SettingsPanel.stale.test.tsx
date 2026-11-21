// 2026-10-18: the classifier role read 'plugin' after the operator saved TypeSafe from a Settings tab
// left open since before TypeSafe was configured (its template list still lacked the TypeSafe
// template). The page now re-reads roles and templates when the tab regains focus, and a refresh
// replaces a card's draft only when the server's values changed.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getPanel } from '@/config/panels';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { useHealthStore } from '@/stores/healthStore';
import { useLabelingStore } from '@/stores/labelingStore';

import { SettingsPanel } from './SettingsPanel';

const role = (r: string, extra: Record<string, unknown> = {}) => ({
  role: r, configured: false, protocol: null, base_url: null, model_id: null, api_key: null,
  has_api_key: false, inherit_from_judge: r === 'generation' || r === 'embeddings', use_mode: 'own', effective_role: null, ...extra,
});
const classifier = (protocol: string) =>
  role('classifier', { configured: true, protocol, base_url: 'https://api.typesafe.ai/v1/systemone', model_id: 'jev-latest', api_key: 'api...fe9e', has_api_key: true });
const template = (ref: string, protocol: string) => ({ id: `dt_${ref}`, ref, name: ref, version: 1, protocol, bound_model_id: null, used: true });

function setup(server: { protocol: string; templates: unknown[] }) {
  useHealthStore.setState({ health: HEALTH as never });
  useLabelingStore.setState({ templates: [] });
  return mockFetch({
    'GET /api/v1/settings': [],
    'GET /api/v1/endpoint-roles': () => ({ json: [classifier(server.protocol), role('judge'), role('generation'), role('embeddings')] }),
    'PUT /api/v1/endpoint-roles/classifier': (b: unknown) => {
      server.protocol = (b as { protocol: string }).protocol;
      return { json: classifier(server.protocol) };
    },
    'GET /api/v1/decision-templates': () => ({ json: server.templates }),
    'GET /api/v1/agent-access': { mcp_public_url: null, mcp: { reachable: false, categories: null, reason: 'x' }, gated_actions: {}, label_threshold: 1, label_window_hours: 1, approval_ttl_hours: 1, activity: { window: 'h', identities: [], sessions: 0, requests: 0 } },
    'GET /api/v1/settings/shortcut-level': { margin_pp: 10, source: 'code_default', level: { margin_pp: 10, source: 'code_default', set_by: null, set_at: null, reason: null }, history: [] },
  });
}

const protocolOf = (card: HTMLElement) => (within(card).getByLabelText('Protocol') as HTMLSelectElement).value;

describe('Settings stays current in a long-open tab', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('a role and a template changed elsewhere appear when the tab regains focus, and Save sends them', async () => {
    const server = { protocol: 'plugin', templates: [template('jev/noul-bare-v1@1', 'openai_scoring')] };
    const { calls } = setup(server);
    render(<SettingsPanel panel={getPanel('settings')} />);
    const card = await screen.findByTestId('role-classifier');
    await waitFor(() => expect(protocolOf(card)).toBe('plugin'));
    // Another tab (or an agent) moves the role to TypeSafe and creates its template.
    server.protocol = 'typesafe_systemone';
    server.templates = [...server.templates, template('typesafe/humor-funny-choice@1', 'typesafe_systemone')];
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(protocolOf(card)).toBe('typesafe_systemone'));
    expect(await within(card).findByText(/typesafe\/humor-funny-choice@1/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Save the classifier endpoint' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT').map((c) => (c.body as { protocol: string }).protocol)).toEqual(['typesafe_systemone']);
    expect(server.protocol).toBe('typesafe_systemone');
  });

  it('a refresh that returns the same values keeps an unsaved protocol choice', async () => {
    const server = { protocol: 'plugin', templates: [] };
    const { calls } = setup(server);
    render(<SettingsPanel panel={getPanel('settings')} />);
    const card = await screen.findByTestId('role-classifier');
    await waitFor(() => expect(protocolOf(card)).toBe('plugin'));
    fireEvent.change(within(card).getByLabelText('Protocol'), { target: { value: 'typesafe_systemone' } });
    const before = calls.filter((c) => c.path === '/api/v1/endpoint-roles').length;
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/endpoint-roles').length).toBe(before + 1));
    await act(async () => {
      await Promise.resolve();
    });
    expect(protocolOf(card)).toBe('typesafe_systemone');
    fireEvent.click(within(card).getByRole('button', { name: 'Save the classifier endpoint' }));
    await waitFor(() => expect(server.protocol).toBe('typesafe_systemone'));
    await waitFor(() => expect(protocolOf(card)).toBe('typesafe_systemone'));
  });
});

// Feature 014 (operator directive 2026-10-20): the Settings PIN in the REAL <App/>. A Settings write
// answered 403 PIN_REQUIRED opens the unlock dialog App mounts; the PIN goes to /pin/session once;
// the write is sent again ONCE, carrying the session header. Removing <PinPrompt/> from App, the
// client's retry or the header turns these red (controls in 0xcc/reviews/014_*).
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { PIN_SESSION_HEADER, setPinSessionToken } from '@/api/pinSession';
import { usePinStore } from '@/stores/pinStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';

const TOKEN = '1999999999.abcdef0123456789.mac';
const STATUS = (configured: boolean, session_valid = false) => ({
  configured,
  bypass_active: false,
  session_valid,
  session_ttl_s: 1800,
  header: PIN_SESSION_HEADER,
});
const PIN_REQUIRED = {
  status: 403,
  json: { error: { code: 'PIN_REQUIRED', message: 'Settings are locked with a PIN. Enter the Settings PIN to change them.', details: {} } },
};
const NAME = { key: 'operator_name', value: 'Ada', is_sensitive: false, is_set: true, category: 'identity', description: '', type: 'string' };

const BASE = {
  'GET /api/health': HEALTH,
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/settings': [NAME],
  'GET /api/v1/endpoint-roles': [],
  'GET /api/v1/agent-access': {
    mcp_public_url: null,
    mcp: { reachable: false, categories: null, reason: 'not set' },
    gated_actions: {},
    label_threshold: 5000,
    label_window_hours: 24,
    approval_ttl_hours: 24,
    activity: { window: 'last hour', identities: [], sessions: 0, requests: 0 },
  },
  'GET /api/v1/settings/environment': { settings: [], history: [] },
  'GET /api/v1/settings/shortcut-level': { margin_pp: 10, source: 'code_default', level: { margin_pp: 10, source: 'code_default', set_by: null, set_at: null, reason: null }, history: [] },
};

function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  const headers = (init?.headers ?? {}) as Record<string, string>;
  return headers[name];
}

beforeEach(() => {
  setPinSessionToken(null);
  usePinStore.setState({ status: null, error: null, notice: null, prompt: null });
  useSettingsStore.setState({ notice: null, error: null, warning: null });
  window.history.replaceState(null, '', '#/settings');
});
afterEach(() => {
  vi.unstubAllGlobals();
  setPinSessionToken(null);
  window.history.replaceState(null, '', '#/');
});

describe('a locked Settings write asks for the PIN and is sent again (014)', () => {
  it('opens the dialog, unlocks once and resends the write once with the session header', async () => {
    let puts = 0;
    const { fn, calls } = mockFetch({
      ...BASE,
      'GET /api/v1/settings/pin': STATUS(true),
      'PUT /api/v1/settings/operator_name': () => (++puts === 1 ? PIN_REQUIRED : { json: { ...NAME, value: 'Grace' } }),
      'POST /api/v1/settings/pin/session': { token: TOKEN, expires_at: '2033-05-18T03:33:19Z', header: PIN_SESSION_HEADER },
    });
    render(<App />);
    const card = await screen.findByTestId('operator-name');
    fireEvent.change(within(card).getByLabelText('Name'), { target: { value: 'Grace' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save your name' }));

    const input = await screen.findByLabelText('PIN');
    expect(screen.getByText(/Settings are locked with a PIN/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '4711' } });
    fireEvent.click(screen.getByTestId('pin-unlock'));

    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/api/v1/settings/pin/session')).toEqual([
      { method: 'POST', path: '/api/v1/settings/pin/session', body: { pin: '4711' } },
    ]);
    const writes = fn.mock.calls.filter(([path, init]) => path === '/api/v1/settings/operator_name' && init?.method === 'PUT');
    expect(writes).toHaveLength(2);
    expect(headerOf(writes[0][1], PIN_SESSION_HEADER)).toBeUndefined();
    expect(headerOf(writes[1][1], PIN_SESSION_HEADER)).toBe(TOKEN);
    expect(writes[1][1]?.body).toBe(JSON.stringify({ value: 'Grace' }));
    expect(screen.queryByLabelText('PIN')).toBeNull();
  });

  it('a wrong PIN stays in the dialog and sends nothing more', async () => {
    const { fn } = mockFetch({
      ...BASE,
      'GET /api/v1/settings/pin': STATUS(true),
      'PUT /api/v1/settings/operator_name': () => PIN_REQUIRED,
      'POST /api/v1/settings/pin/session': () => ({ status: 403, json: { error: { code: 'PIN_WRONG', message: 'That Settings PIN is wrong.', details: {} } } }),
    });
    render(<App />);
    const card = await screen.findByTestId('operator-name');
    fireEvent.click(within(card).getByRole('button', { name: 'Save your name' }));
    fireEvent.change(await screen.findByLabelText('PIN'), { target: { value: '0000' } });
    fireEvent.click(screen.getByTestId('pin-unlock'));
    expect(await screen.findByText('That Settings PIN is wrong.')).toBeInTheDocument();
    const writes = fn.mock.calls.filter(([path, init]) => path === '/api/v1/settings/operator_name' && init?.method === 'PUT');
    expect(writes).toHaveLength(1);
  });

  it('cancelling leaves the write refused, with the reason shown, and does not resend it', async () => {
    const { fn } = mockFetch({
      ...BASE,
      'GET /api/v1/settings/pin': STATUS(true),
      'PUT /api/v1/settings/operator_name': () => PIN_REQUIRED,
    });
    render(<App />);
    const card = await screen.findByTestId('operator-name');
    fireEvent.click(within(card).getByRole('button', { name: 'Save your name' }));
    await screen.findByLabelText('PIN');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('Settings are locked with a PIN. Enter the Settings PIN to change them.')).toBeInTheDocument();
    const writes = fn.mock.calls.filter(([path, init]) => path === '/api/v1/settings/operator_name' && init?.method === 'PUT');
    expect(writes).toHaveLength(1);
  });

  it('a held session goes with every request', async () => {
    setPinSessionToken(TOKEN);
    const { fn } = mockFetch({ ...BASE, 'GET /api/v1/settings/pin': STATUS(true, true), 'PUT /api/v1/settings/operator_name': NAME });
    render(<App />);
    const card = await screen.findByTestId('operator-name');
    fireEvent.click(within(card).getByRole('button', { name: 'Save your name' }));
    await waitFor(() => expect(fn.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
    const put = fn.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(headerOf(put?.[1], PIN_SESSION_HEADER)).toBe(TOKEN);
    expect(screen.queryByLabelText('PIN')).toBeNull();
  });
});

describe('the Settings PIN card (014)', () => {
  it('is on the Settings screen of the real app and sets a PIN, once, after two equal entries', async () => {
    const { calls } = mockFetch({
      ...BASE,
      'GET /api/v1/settings/pin': STATUS(false),
      'PUT /api/v1/settings/pin': { token: TOKEN, expires_at: '2033-05-18T03:33:19Z', header: PIN_SESSION_HEADER },
    });
    render(<App />);
    const card = await screen.findByTestId('settings-pin');
    expect(await within(card).findByText(/No PIN is set/)).toBeInTheDocument();
    const set = within(card).getByRole('button', { name: 'Set the PIN' });
    fireEvent.change(within(card).getByLabelText('PIN'), { target: { value: '4711' } });
    fireEvent.change(within(card).getByLabelText('The new PIN again'), { target: { value: '4712' } });
    expect(set).toBeDisabled();
    expect(within(card).getByText('The two entries differ.')).toBeInTheDocument();
    fireEvent.change(within(card).getByLabelText('The new PIN again'), { target: { value: '4711' } });
    fireEvent.click(set);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/settings/pin')).toBe(true));
    expect(calls.filter((c) => c.path === '/api/v1/settings/pin' && c.method === 'PUT')).toEqual([
      { method: 'PUT', path: '/api/v1/settings/pin', body: { pin: '4711' } },
    ]);
  });

  it('changes the PIN with the current one, and removes it with the current one', async () => {
    const { calls } = mockFetch({
      ...BASE,
      'GET /api/v1/settings/pin': STATUS(true),
      'PUT /api/v1/settings/pin': { token: TOKEN, expires_at: '2033-05-18T03:33:19Z', header: PIN_SESSION_HEADER },
      'POST /api/v1/settings/pin/remove': STATUS(false),
    });
    render(<App />);
    const card = await screen.findByTestId('settings-pin');
    expect(await within(card).findByText('A PIN is set. Settings is locked.')).toBeInTheDocument();
    fireEvent.change(within(card).getByLabelText('Current PIN'), { target: { value: '4711' } });
    fireEvent.change(within(card).getByLabelText('New PIN'), { target: { value: '135792' } });
    fireEvent.change(within(card).getByLabelText('The new PIN again'), { target: { value: '135792' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Change the PIN' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/v1/settings/pin')).toBe(true));
    expect(calls.filter((c) => c.path === '/api/v1/settings/pin' && c.method === 'PUT')).toEqual([
      { method: 'PUT', path: '/api/v1/settings/pin', body: { pin: '135792', current_pin: '4711' } },
    ]);

    fireEvent.change(within(card).getByLabelText('Current PIN'), { target: { value: '135792' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Remove the PIN' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/settings/pin/remove')).toBe(true));
    expect(calls.filter((c) => c.path === '/api/v1/settings/pin/remove')).toEqual([
      { method: 'POST', path: '/api/v1/settings/pin/remove', body: { current_pin: '135792' } },
    ]);
  });
});

// The Approvals screen (operator, 2026-10-20: "I looked for Approvals in the menu and found nothing").
// Runs the REAL <App/> (panel registry, sidebar, banner poll, store, API client, PIN dialog) against
// a fetch stub. Removing the registry line, the sidebar badge wiring or the approve call turns these
// red (controls in 0xcc/reviews/approvals_screen_2026-10-20_controls.md).
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { PIN_SESSION_HEADER, setPinSessionToken } from '@/api/pinSession';
import { ApprovalsPanel } from '@/components/approvals/ApprovalsPanel';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { PANELS } from '@/config/panels';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { usePinStore } from '@/stores/pinStore';
import type { Approval } from '@/types/api';

import { HEALTH, mockFetch } from './fetchMock';

const PENDING_PATH = '/api/v1/approvals?status=pending';

const request = (id: string, action: string, summary: string, status: Approval['status'] = 'pending') => ({
  id, action, target: 't', summary, payload: {}, request_digest: '0', requested_by: 'agent:dataworks-mcp', status,
  expires_at: new Date(Date.now() + 2 * 3600_000).toISOString(), decided_by: status === 'pending' ? null : 'Ada',
  created_at: new Date().toISOString(), archived_at: null,
});

/** A small approvals server: pending and decided requests, and the approve/reject routes. */
function server(initial: ReturnType<typeof request>[], approve?: (id: string) => { status?: number; json?: unknown } | undefined) {
  let all = [...initial];
  const decide = (id: string, status: Approval['status']) => {
    all = all.map((a) => (a.id === id ? { ...a, status, decided_by: 'Ada' } : a));
    return { json: all.find((a) => a.id === id) };
  };
  const routes: Record<string, unknown> = {
    'GET /api/health': HEALTH,
    'GET /api/v1/jobs': { jobs: [] },
    'GET /api/v1/datasets': { items: [], total: 0 },
    [`GET ${PENDING_PATH}`]: () => ({ json: { approvals: all.filter((a) => a.status === 'pending') } }),
    'GET /api/v1/approvals': () => ({ json: { approvals: all } }),
  };
  for (const a of initial) {
    routes[`POST /api/v1/approvals/${a.id}/approve`] = () => approve?.(a.id) ?? decide(a.id, 'executed');
    routes[`POST /api/v1/approvals/${a.id}/reject`] = () => decide(a.id, 'rejected');
  }
  return mockFetch(routes);
}

const PUSH = request('apr_push', 'hub_push', 'Push version 3 of humor to the Hub');
const LABEL = request('apr_label', 'agent_label_rows', 'Label 9,000 rows of humor v3');
const OLD = request('apr_old', 'version_delete', 'Delete version 1 of humor', 'rejected');

beforeEach(() => {
  useApprovalsStore.setState({ pending: [], error: null, loadError: null, history: [], historyIncludeArchived: false, historyError: null });
  window.history.replaceState(null, '', '#/');
});
afterEach(() => {
  vi.unstubAllGlobals();
  setPinSessionToken(null);
  window.history.replaceState(null, '', '#/');
});

describe('Approvals is a screen in the menu', () => {
  it('is registered in the live panel registry, in the main menu, with its component', () => {
    const def = PANELS.find((p) => p.id === 'approvals');
    expect(def).toBeDefined();
    expect(def && 'foot' in def ? def.foot : false).toBe(false);
    expect(def?.label).toBe('Approvals');
    expect(PANEL_COMPONENTS.approvals).toBe(ApprovalsPanel);
  });

  it('the menu entry opens the Approvals screen with the waiting and the decided requests', async () => {
    server([PUSH, OLD]);
    render(<App />);
    const nav = within(screen.getByRole('navigation', { name: 'Screens' })).getByTestId('nav-approvals');
    fireEvent.click(nav);
    const shown = screen.getByTestId('screen-approvals');
    expect(within(shown).getByRole('heading', { level: 1, name: 'Approvals' })).toBeInTheDocument();
    expect(window.location.hash).toBe('#/approvals');
    const waiting = within(shown).getByTestId('approvals-pending');
    await waitFor(() => expect(within(waiting).getAllByTestId('approval-row')).toHaveLength(1));
    expect(within(waiting).getByText('Push version 3 of humor to the Hub')).toBeInTheDocument();
    const history = within(shown).getByTestId('approval-history');
    expect(await within(history).findByText('Delete version 1 of humor')).toBeInTheDocument();
  });

  it('says so in plain words when nothing waits', async () => {
    server([OLD]);
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    expect(await screen.findByText('No agent requests are waiting.')).toBeInTheDocument();
    expect(screen.queryByTestId('approval-row')).toBeNull();
  });
});

describe('the menu badge counts the waiting requests', () => {
  it('equals the number of pending approvals, on the sidebar and the mobile tabs', async () => {
    server([PUSH, LABEL, OLD]);
    render(<App />);
    const nav = screen.getByTestId('nav-approvals');
    await waitFor(() => expect(within(nav).getByTestId('badge-approvals')).toHaveTextContent(/^2$/));
    expect(within(nav).getByTestId('badge-approvals')).toHaveAttribute('aria-label', '2 waiting');
    expect(within(screen.getByTestId('mobile-tabs')).getByRole('button', { name: 'Approvals (2)' })).toBeInTheDocument();
  });

  it('shows no badge when nothing waits, and drops by one after an approval', async () => {
    server([PUSH, LABEL]);
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    const nav = screen.getByTestId('nav-approvals');
    await waitFor(() => expect(within(nav).getByTestId('badge-approvals')).toHaveTextContent(/^2$/));
    fireEvent.click(screen.getByRole('button', { name: 'Approve hub push' }));
    await waitFor(() => expect(within(nav).getByTestId('badge-approvals')).toHaveTextContent(/^1$/));
    fireEvent.click(screen.getByRole('button', { name: 'Approve agent labelling over the row limit' }));
    await waitFor(() => expect(within(nav).queryByTestId('badge-approvals')).toBeNull());
  });

  it('the screen does not add a second poll: one pending load on mount, from the banner', async () => {
    const { calls } = server([PUSH]);
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    await screen.findAllByTestId('approval-row');
    await screen.findByTestId('approval-history');
    expect(calls.filter((c) => c.path === PENDING_PATH)).toHaveLength(1);
  });
});

describe('approving from the Approvals screen', () => {
  it('calls the approve route once with the request id, then moves it to the decided list', async () => {
    const { calls } = server([PUSH, LABEL]);
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    const rows = await screen.findAllByTestId('approval-row');
    expect(rows).toHaveLength(2); // the banner draws nothing here: no request listed twice
    expect(screen.queryByTestId('approvals-banner')).toBeNull();
    const row = rows.find((r) => within(r).queryByText('Label 9,000 rows of humor v3'))!;
    fireEvent.click(within(row).getByRole('button', { name: 'Approve agent labelling over the row limit' }));

    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(1));
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([{ method: 'POST', path: '/api/v1/approvals/apr_label/approve', body: undefined }]);
    const history = screen.getByTestId('approval-history');
    expect(await within(history).findByText('Label 9,000 rows of humor v3')).toBeInTheDocument();
  });

  it('reject sends the reject route once, for that id only', async () => {
    const { calls } = server([PUSH, LABEL]);
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    const rows = await screen.findAllByTestId('approval-row');
    const row = rows.find((r) => within(r).queryByText('Push version 3 of humor to the Hub'))!;
    fireEvent.click(within(row).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(1));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { method: 'POST', path: '/api/v1/approvals/apr_push/reject', body: { reason: 'Rejected by the operator.' } },
    ]);
  });

  it('approving a settings change asks for the Settings PIN, then sends the approval again with the session (014)', async () => {
    usePinStore.setState({ status: null, error: null, notice: null, prompt: null });
    const SECRET = request('apr_secret', 'secret_write', 'Write the secret setting hf_token');
    let approves = 0;
    const token = '1999999999.abcdef0123456789.mac';
    const { fn } = server([SECRET], () =>
      ++approves === 1
        ? { status: 403, json: { error: { code: 'PIN_REQUIRED', message: 'Settings are locked with a PIN. Enter the Settings PIN to change them.', details: {} } } }
        : undefined, // the server decides it (status executed)
    );
    // the PIN unlock route
    const routes = fn.getMockImplementation()!;
    fn.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === '/api/v1/settings/pin/session'
        ? new Response(JSON.stringify({ token, expires_at: '2033-05-18T03:33:19Z', header: PIN_SESSION_HEADER }), { status: 200 })
        : routes(input, init),
    );
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Approve secret write (token or api key)' }));
    fireEvent.change(await screen.findByLabelText('PIN'), { target: { value: '4711' } });
    fireEvent.click(screen.getByTestId('pin-unlock'));
    await waitFor(() => expect(screen.queryByTestId('approval-row')).toBeNull());
    const sent = fn.mock.calls.filter(([path, init]) => path === '/api/v1/approvals/apr_secret/approve' && init?.method === 'POST');
    expect(sent).toHaveLength(2);
    expect((sent[1][1]?.headers as Record<string, string>)[PIN_SESSION_HEADER]).toBe(token);
  });
});

describe('the banner points to the Approvals screen', () => {
  it('on another screen it shows the requests and opens Approvals from its link', async () => {
    server([PUSH]);
    window.history.replaceState(null, '', '#/datasets');
    render(<App />);
    const banner = await screen.findByTestId('approvals-banner');
    fireEvent.click(within(banner).getByRole('button', { name: 'Open Approvals' }));
    await waitFor(() => expect(screen.getByTestId('screen-approvals')).toBeInTheDocument());
    expect(screen.queryByTestId('approvals-banner')).toBeNull();
    expect(screen.getAllByTestId('approval-row')).toHaveLength(1);
  });
});

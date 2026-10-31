// ENH-008 (miSuite specs/ENH-008-approvals-pages/BUILD_SPEC.md sections 2 and 5, miDataworks A1,
// A4-A6): the banner on every screen (at most 3 cards, "and N more", Collapse/Expand, the region
// label, a load failure said), the card's fields and the inline reason, the History columns with a
// decided request in them, Show older, local time with the instant in the tooltip, and hostile text
// rendered as text. Runs the real stores and API client against a fetch stub; the App-level tests
// run the real <App/>, so removing the banner mount or the nav entry turns them red (controls in
// 0xcc/reviews/enh008_approvals_alignment_2026-10-28.md).
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { HISTORY_COLUMNS, ApprovalHistoryCard, decidedBy, resultText } from '@/components/approvals/ApprovalHistoryCard';
import { ApprovalsBanner, BANNER_COLLAPSED_KEY } from '@/components/approvals/ApprovalsBanner';
import { HISTORY_PAGE, useApprovalsStore } from '@/stores/approvalsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import type { Approval } from '@/types/api';
import { formatLocal, isoInstant } from '@/utils/format';

const HOUR = 3600_000;
const SHOWN = { archived_at: null, archived_by: null, archive_reason: null };

function request(id: string, overrides: Partial<Approval> = {}): Approval {
  return {
    id, action: 'hub_push', target: 't', summary: `Summary of ${id}`, payload: { repo_id: 'owner/humor', private: true },
    request_digest: '0', requested_by: 'agent:dataworks-mcp', status: 'pending',
    expires_at: new Date(Date.now() + 5 * HOUR).toISOString(), decided_by: null, decided_at: null, reason: null,
    created_at: '2026-10-28T13:05:00Z', ...SHOWN, ...overrides,
  } as Approval;
}

const resetStore = () =>
  useApprovalsStore.setState({
    pending: [], error: null, errorId: null, loadError: null, history: [], historyHasOlder: false,
    historyIncludeArchived: false, historyError: null,
  });

beforeEach(() => {
  resetStore();
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '#/');
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '#/');
});

describe('time on the approvals surfaces (A5)', () => {
  it('is the browser-local time with its zone, the ISO instant in UTC beside it, "unknown" for a bad value', () => {
    const iso = '2026-10-28T13:05:00Z';
    const expected = new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
    }).format(new Date(iso));
    expect(formatLocal(iso)).toBe(expected);
    expect(isoInstant('2026-10-28T09:05:00-04:00')).toBe('2026-10-28T13:05:00.000Z');
    for (const bad of [null, undefined, '', 'not a date']) {
      expect(formatLocal(bad)).toBe('unknown');
      expect(isoInstant(bad)).toBeUndefined();
    }
    expect(formatLocal('not a date')).not.toContain('Invalid');  });

  it('does not throw: timeZoneName cannot sit beside dateStyle/timeStyle (BUILD_SPEC 2.7 correction)', () => {
    expect(() => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZoneName: 'short' })).toThrow(TypeError);
    for (const iso of ['2026-10-28T13:05:00Z', '2026-01-01T00:00:00Z', '2026-07-04T23:59:59.999Z']) {
      expect(() => formatLocal(iso)).not.toThrow();
      expect(formatLocal(iso)).toMatch(/2026/);
    }
  });
});

describe('the banner (A1)', () => {
  const five = [
    request('apr_late', { expires_at: new Date(Date.now() + 20 * HOUR).toISOString() }),
    request('apr_soon', { expires_at: new Date(Date.now() + 1 * HOUR).toISOString() }),
    request('apr_mid', { expires_at: new Date(Date.now() + 5 * HOUR).toISOString() }),
    request('apr_next', { expires_at: new Date(Date.now() + 2 * HOUR).toISOString() }),
    request('apr_last', { expires_at: new Date(Date.now() + 23 * HOUR).toISOString() }),
  ];

  it('is a labelled region showing the 3 that expire soonest, then "and N more waiting"', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: five } });
    render(<ApprovalsBanner />);
    const region = await screen.findByRole('region', { name: 'Agent requests waiting for your approval' });
    expect(region).toHaveAttribute('data-testid', 'approvals-banner');
    expect(within(region).getByRole('heading', { name: '5 agent requests waiting for your approval' })).toHaveAttribute('aria-live', 'polite');
    const cards = within(region).getAllByTestId('approval-row');
    expect(cards.map((c) => within(c).getByTestId('approval-id').textContent)).toEqual(['apr_soon', 'apr_next', 'apr_mid']);
    expect(within(region).getByTestId('approvals-banner-more')).toHaveTextContent('and 2 more waiting — Open Approvals');
  });

  it('shows no "more" line at 3 or fewer', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: five.slice(0, 3) } });
    render(<ApprovalsBanner />);
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(3));
    expect(screen.queryByTestId('approvals-banner-more')).toBeNull();
  });

  it('collapses to its heading for the session, and a new request expands it again', async () => {
    let list = five.slice(0, 2);
    mockFetch({ 'GET /api/v1/approvals': () => ({ json: { approvals: list } }) });
    const first = render(<ApprovalsBanner />);
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(2));
    fireEvent.click(screen.getByRole('button', { name: 'Collapse' }));
    expect(screen.queryByTestId('approval-row')).toBeNull();
    expect(screen.getByRole('heading', { name: '2 agent requests waiting for your approval' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand' })).toHaveAttribute('aria-expanded', 'false');
    expect(JSON.parse(window.sessionStorage.getItem(BANNER_COLLAPSED_KEY)!)).toEqual(['apr_soon', 'apr_late']);

    // A reload (a fresh mount) keeps the choice while the same requests wait.
    first.unmount();
    resetStore();
    render(<ApprovalsBanner />);
    await screen.findByRole('heading', { name: '2 agent requests waiting for your approval' });
    expect(screen.queryByTestId('approval-row')).toBeNull();

    // A new request arrives at the next poll: the banner opens again.
    list = five.slice(0, 3);
    await act(async () => {
      await useApprovalsStore.getState().fetchPending();
    });
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(3));
    expect(screen.getByRole('button', { name: 'Collapse' })).toBeInTheDocument();
  });

  it('a failed load is said, never silent', async () => {
    mockFetch({ 'GET /api/v1/approvals': () => ({ status: 503, json: { error: { code: 'UNAVAILABLE', message: 'PostgreSQL is down.', details: {} } } }) });
    render(<ApprovalsBanner />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read the approval queue: PostgreSQL is down.');
  });

  it('is in the shell on every other screen, and not on Approvals (the mount in App)', async () => {
    mockFetch({
      'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] }, 'GET /api/v1/datasets': { items: [], total: 0 },
      'GET /api/v1/approvals': { approvals: [request('apr_1')] },
    });
    window.history.replaceState(null, '', '#/datasets');
    render(<App />);
    const region = await screen.findByRole('region', { name: 'Agent requests waiting for your approval' });
    expect(within(screen.getByTestId('screen-datasets')).getByTestId('approvals-banner')).toBe(region);
    fireEvent.click(screen.getByTestId('nav-approvals'));
    await waitFor(() => expect(screen.getByTestId('screen-approvals')).toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'Agent requests waiting for your approval' })).toBeNull();
    expect(within(screen.getByTestId('nav-approvals')).getByTestId('nav-badge-approvals')).toHaveAttribute('title', '1 waiting');
  });
});

describe('the waiting card (section 2.1)', () => {
  it('shows its fields in order: action and pill, who asked, when, facts, arguments, id, buttons', async () => {
    const r = request('apr_card', { payload: { repo_id: 'owner/humor', options: { private: true } } });
    mockFetch({ 'GET /api/v1/approvals': { approvals: [r] } });
    render(<ApprovalsBanner />);
    const card = await screen.findByTestId('approval-row');
    const text = card.textContent ?? '';
    const order = ['Hub push', 'Waiting', 'asked by', 'asked ' + formatLocal(r.created_at), 'expires in 5 h', 'Summary of apr_card', 'All arguments the agent sent', 'Requestapr_card', 'Approve hub push', 'Reject'];
    const at = order.map((t) => text.indexOf(t));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(within(card).getByTestId('approval-pill')).toHaveTextContent('Waiting');
    expect(within(card).getByTestId('local-time')).toHaveAttribute('title', '2026-10-28T13:05:00.000Z');
    expect(within(card).getByTestId('approval-expiry')).toHaveAttribute('title', new Date(r.expires_at).toISOString());
    const args = within(card).getByTestId('approval-arguments');
    expect(args.tagName).toBe('DETAILS');
    expect(args).not.toHaveAttribute('open');
    expect(args).toHaveTextContent('repo_id');
    expect(args).toHaveTextContent('owner/humor');
    expect(args).toHaveTextContent('"private": true');
    expect(within(card).getByRole('button', { name: 'Copy request id apr_card' })).toBeInTheDocument();
  });

  it('moves focus to the next card after a decision that went through', async () => {
    let list = [request('apr_one', { expires_at: new Date(Date.now() + HOUR).toISOString() }), request('apr_two')];
    mockFetch({
      'GET /api/v1/approvals': () => ({ json: { approvals: list } }),
      'POST /api/v1/approvals/apr_one/approve': () => {
        list = list.slice(1);
        return { json: { ...list[0], id: 'apr_one', status: 'executed' } };
      },
    });
    render(<ApprovalsBanner />);
    const [first] = await screen.findAllByTestId('approval-row');
    fireEvent.click(within(first).getByRole('button', { name: 'Approve hub push' }));
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId('approval-row')).toHaveFocus());
    expect(within(screen.getByTestId('approval-row')).getByTestId('approval-id')).toHaveTextContent('apr_two');
  });

  it('says "asked by an agent" when no name was recorded', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [request('apr_anon', { requested_by: '' })] } });
    render(<ApprovalsBanner />);
    expect(await screen.findByText('asked by an agent')).toBeInTheDocument();
  });

  it('Reject opens an optional reason: a typed one is sent, a blank one sends the default', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/approvals': { approvals: [request('apr_a'), request('apr_b')] },
      'POST /api/v1/approvals/apr_a/reject': request('apr_a', { status: 'rejected' }),
      'POST /api/v1/approvals/apr_b/reject': request('apr_b', { status: 'rejected' }),
    });
    render(<ApprovalsBanner />);
    const [a, b] = await screen.findAllByTestId('approval-row');
    fireEvent.click(within(a).getByRole('button', { name: 'Reject' }));
    const field = within(a).getByRole('textbox', { name: 'Reason (optional)' });
    expect(field).toHaveAttribute('placeholder', 'Reason (optional)');
    expect(field).toHaveAttribute('maxLength', '1000');
    expect(field).toHaveFocus();
    fireEvent.change(field, { target: { value: 'not this repo' } });
    fireEvent.click(within(a).getByRole('button', { name: 'Confirm reject' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1));
    fireEvent.click(within(b).getByRole('button', { name: 'Reject' }));
    fireEvent.click(within(b).getByRole('button', { name: 'Cancel' }));
    expect(within(b).queryByRole('textbox')).toBeNull();
    fireEvent.click(within(b).getByRole('button', { name: 'Reject' }));
    fireEvent.click(within(b).getByRole('button', { name: 'Confirm reject' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { method: 'POST', path: '/api/v1/approvals/apr_a/reject', body: { reason: 'not this repo' } },
      { method: 'POST', path: '/api/v1/approvals/apr_b/reject', body: { reason: 'Rejected by the operator.' } },
    ]);
  });

  it('disables both buttons while a decision is in flight, and shows a refused one inside the card', async () => {
    let release: (r: Response) => void = () => {};
    const r = request('apr_slow');
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'POST') return new Promise<Response>((resolve) => { release = resolve; });
      return new Response(JSON.stringify({ approvals: [r] }), { status: 200 });
    }));
    render(<ApprovalsBanner />);
    const card = await screen.findByTestId('approval-row');
    fireEvent.click(within(card).getByRole('button', { name: 'Approve hub push' }));
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Approve hub push' })).toBeDisabled());
    expect(within(card).getByRole('button', { name: 'Reject' })).toBeDisabled();
    await act(async () => {
      release(new Response(JSON.stringify({ error: { code: 'APPROVAL_NOT_PENDING', message: 'Approval apr_slow is executing.', details: { status: 'executing' } } }), { status: 409 }));
    });
    const shown = await screen.findByTestId('approval-row');
    expect(within(shown).getByRole('alert')).toHaveTextContent('Approval apr_slow is executing. The list has been refreshed.');
    expect(within(shown).getByRole('button', { name: 'Approve hub push' })).toBeEnabled();
    await waitFor(() => expect(shown).toHaveFocus());
  });
});

describe('History (A4)', () => {
  it('a rejected request with a typed reason appears in History with the shared columns', async () => {
    let all: Approval[] = [request('apr_push', { summary: 'Push version 3 of humor to the Hub' })];
    const { calls } = mockFetch({
      'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] },
      'GET /api/v1/approvals?status=pending': () => ({ json: { approvals: all.filter((a) => a.status === 'pending') } }),
      'GET /api/v1/approvals': () => ({ json: { approvals: all.filter((a) => a.status !== 'pending') } }),
      'POST /api/v1/approvals/apr_push/reject': (body: unknown) => {
        const reason = (body as { reason: string }).reason;
        all = all.map((a) => ({ ...a, status: 'rejected', decided_by: 'operator', decided_at: '2026-10-28T14:00:00Z', reason }));
        return { json: all[0] };
      },
    });
    window.history.replaceState(null, '', '#/approvals');
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Waiting for you (1)' })).toBeInTheDocument();
    const history = screen.getByTestId('approval-history');
    expect(within(history).getByRole('heading', { name: 'History' })).toBeInTheDocument();
    expect(await within(history).findByText('No decided requests yet.')).toBeInTheDocument();

    const card = screen.getByTestId('approval-row');
    fireEvent.click(within(card).getByRole('button', { name: 'Reject' }));
    fireEvent.change(within(card).getByRole('textbox', { name: 'Reason (optional)' }), { target: { value: 'x' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Confirm reject' }));

    const row = await within(history).findByTestId('approval-history-item');
    expect(screen.getByRole('heading', { name: 'Waiting for you (0)' })).toBeInTheDocument();
    expect(screen.getByText('No agent requests are waiting.')).toBeInTheDocument();
    expect(within(history).getAllByRole('columnheader').slice(0, HISTORY_COLUMNS.length).map((h) => h.textContent)).toEqual([...HISTORY_COLUMNS]);
    const cell = (column: string) => row.querySelector(`[data-column="${column}"]`) as HTMLElement;
    expect(cell('Outcome')).toHaveTextContent('Rejected');
    expect(cell('Action')).toHaveTextContent('Hub push');
    expect(cell('Asked by')).toHaveTextContent('asked by agentdataworks-mcp');
    expect(within(cell('Asked')).getByTestId('local-time')).toHaveAttribute('title', '2026-10-28T13:05:00.000Z');
    expect(cell('Decided by')).toHaveTextContent('operator');
    expect(within(cell('Decided')).getByTestId('local-time')).toHaveTextContent(formatLocal('2026-10-28T14:00:00Z'));
    expect(within(cell('Decided')).getByTestId('local-time')).toHaveAttribute('title', '2026-10-28T14:00:00.000Z');
    expect(cell('Reason')).toHaveTextContent(/^Reason:x$/);
    expect(cell('Result')).toHaveTextContent('—');
    expect(cell('Id')).toHaveTextContent('apr_push');
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: '/api/v1/approvals/apr_push/reject', body: { reason: 'x' } }]);
    expect(calls.some((c) => c.path === `/api/v1/approvals?decided=true&limit=${HISTORY_PAGE}&offset=0`)).toBe(true);
  });

  it('never lists a pending request, even from a server that ignores decided=true', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [request('apr_wait'), request('apr_done', { status: 'executed', decided_at: '2026-10-28T14:00:00Z' })] } });
    render(<ApprovalHistoryCard />);
    const rows = await screen.findAllByTestId('approval-history-item');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('apr_done');
  });

  it('names each outcome, who decided and the result in the shared words', () => {
    expect(decidedBy(request('a', { status: 'expired' }))).toBe('nobody (expired)');
    expect(decidedBy(request('a', { status: 'rejected', decided_by: null }))).toBe('operator');
    expect(resultText(request('a', { status: 'expired' }))).toBe('expired without a decision');
    expect(resultText(request('a', { status: 'failed', error: { code: 'HUB_REFUSED', message: 'The Hub said no.' } }))).toBe('HUB_REFUSED: The Hub said no.');
    expect(resultText(request('a', { status: 'executed', result_kind: 'job', result_id: 'job_7' }))).toBe('job job_7');
    expect(resultText(request('a', { status: 'rejected' }))).toBe('—');
  });

  it('Show older reads the next 200 and stops when a page is short', async () => {
    const decided = (n: number) => request(`apr_${String(n).padStart(3, '0')}`, { status: 'rejected', decided_at: '2026-10-28T14:00:00Z' });
    const { calls } = mockFetch({
      [`GET /api/v1/approvals?decided=true&limit=${HISTORY_PAGE}&offset=0`]: { approvals: Array.from({ length: HISTORY_PAGE }, (_, i) => decided(i)) },
      [`GET /api/v1/approvals?decided=true&limit=${HISTORY_PAGE}&offset=${HISTORY_PAGE}`]: { approvals: [decided(200), decided(201)] },
    });
    render(<ApprovalHistoryCard />);
    await waitFor(() => expect(screen.getAllByTestId('approval-history-item')).toHaveLength(HISTORY_PAGE));
    fireEvent.click(screen.getByRole('button', { name: 'Show older' }));
    await waitFor(() => expect(screen.getAllByTestId('approval-history-item')).toHaveLength(HISTORY_PAGE + 2));
    expect(screen.queryByRole('button', { name: 'Show older' })).toBeNull();
    expect(calls.map((c) => c.path)).toEqual([
      `/api/v1/approvals?decided=true&limit=${HISTORY_PAGE}&offset=0`,
      `/api/v1/approvals?decided=true&limit=${HISTORY_PAGE}&offset=${HISTORY_PAGE}`,
    ]);
  });
});

describe('hostile text is text (A6)', () => {
  const IMG = '<img src=x onerror=alert(1)>';
  const SCRIPT = '<script>window.__pwned=1</script>';
  const evil = `${IMG}${SCRIPT}`;

  it('in every field of a waiting card', async () => {
    const r = request('apr_evil', {
      action: evil, requested_by: `agent:${evil}`, summary: evil,
      payload: { [evil]: evil, nested: { [evil]: [evil] }, facts: { display_name: evil, warning: evil, current: { kind: evil }, proposed: { kind: evil, reason: evil } } },
    });
    mockFetch({
      'GET /api/v1/approvals': { approvals: [r, { ...r, id: 'apr_evil2', action: 'source_annotate' }] },
      'POST /api/v1/approvals/apr_evil/approve': () => ({ status: 409, json: { error: { code: 'X', message: evil, details: {} } } }),
    });
    const { container } = render(<ApprovalsBanner />);
    await waitFor(() => expect(screen.getAllByTestId('approval-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByRole('button', { name: /^Approve/ })[0]);
    await waitFor(() => expect(container.textContent).toContain(`${evil} The list has been refreshed.`));
    for (const d of container.querySelectorAll('details')) d.setAttribute('open', '');
    expect(container.querySelector('img, script')).toBeNull();
    expect(container.textContent).toContain(IMG);
    expect(container.textContent).toContain(SCRIPT);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it('in every History column', async () => {
    mockFetch({
      'GET /api/v1/approvals': {
        approvals: [
          request('apr_h1', { action: evil, requested_by: evil, summary: evil, status: 'rejected', reason: evil, decided_by: evil, decided_at: evil }),
          request('apr_h2', { status: 'failed', error: { code: evil, message: evil }, decided_at: '2026-10-28T14:00:00Z' }),
          request('apr_h3', { status: 'executed', result_kind: evil, result_id: evil, decided_at: '2026-10-28T14:00:00Z' }),
        ],
      },
    });
    const { container } = render(<ApprovalHistoryCard />);
    await waitFor(() => expect(screen.getAllByTestId('approval-history-item')).toHaveLength(3));
    expect(container.querySelector('img, script')).toBeNull();
    expect(container.textContent?.split(IMG).length).toBeGreaterThan(6);
    expect(container.textContent).toContain('unknown'); // a hostile decided_at is not a date
  });
});

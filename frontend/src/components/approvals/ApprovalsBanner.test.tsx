import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useApprovalsStore } from '@/stores/approvalsStore';
import { mockFetch } from '@/test/fetchMock';

import { ApprovalsBanner, expiresIn } from './ApprovalsBanner';

const pending = {
  id: 'apr_1', action: 'secret_write', target: 'x', summary: 'Write the secret setting hf_token', payload: {},
  request_digest: 'abc', requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 3 * 3600_000).toISOString(), decided_by: null, created_at: new Date().toISOString(),
};

const annotation = {
  ...pending,
  id: 'apr_2',
  action: 'source_annotate',
  summary: 'Annotate humor: licence permits. An annotation can unlock a public push.',
  payload: {
    facts: {
      display_name: 'humor', repo_id: 'owner/humor', content_hash: null,
      current: { kind: 'licence', redistribution: 'private_only' },
      proposed: { kind: 'licence', redistribution: 'permits', reason: 'card says MIT' },
      warning: 'An annotation can unlock a public push.',
    },
  },
};

describe('ApprovalsBanner', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useApprovalsStore.setState({ pending: [], error: null, loadError: null });
  });

  it('renders nothing when nothing is pending', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [] } });
    const { container } = render(<ApprovalsBanner />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the identity as an agent, the action, the consequence and the expiry', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [pending] } });
    render(<ApprovalsBanner />);
    expect(await screen.findByText(/1 agent request waiting/)).toBeInTheDocument();
    expect(screen.getByTestId('who-agent')).toHaveTextContent('agentdataworks-mcp');
    expect(screen.getByText('Secret write (token or API key)')).toBeInTheDocument();
    expect(screen.getByText('Write the secret setting hf_token')).toBeInTheDocument();
    expect(screen.getByText('expires in 3 h')).toBeInTheDocument();
  });

  it('approve and reject each send one POST for that approval, buttons naming the action', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/approvals': { approvals: [pending] },
      'POST /api/v1/approvals/apr_1/approve': { ...pending, status: 'executed' },
      'POST /api/v1/approvals/apr_1/reject': { ...pending, status: 'rejected' },
    });
    render(<ApprovalsBanner />);
    fireEvent.click(await screen.findByRole('button', { name: 'Approve secret write (token or api key)' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/approve'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/reject'))).toBe(true));
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([
      { method: 'POST', path: '/api/v1/approvals/apr_1/approve', body: undefined },
      { method: 'POST', path: '/api/v1/approvals/apr_1/reject', body: { reason: 'Rejected by the operator.' } },
    ]);
  });

  it('an expired request says what to do next and the list refreshes', async () => {
    let lists = 0;
    mockFetch({
      'GET /api/v1/approvals': () => {
        lists += 1;
        return { json: { approvals: lists === 1 ? [pending] : [] } };
      },
      'POST /api/v1/approvals/apr_1/approve': () => ({
        status: 409,
        json: { error: { code: 'APPROVAL_NOT_PENDING', message: 'Approval apr_1 is expired.', details: { status: 'expired' } } },
      }),
    });
    render(<ApprovalsBanner />);
    fireEvent.click(await screen.findByRole('button', { name: /^Approve/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This request expired. Ask the agent to try again.');
    await waitFor(() => expect(screen.queryByTestId('approval-row')).toBeNull());
    expect(lists).toBe(2);
  });

  it('a source-annotation card shows the source, current and proposed, and the warning', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [annotation] } });
    render(<ApprovalsBanner />);
    const card = await screen.findByTestId('annotation-card');
    expect(card).toHaveTextContent('humor');
    expect(card).toHaveTextContent('owner/humor');
    expect(card).toHaveTextContent('Current: licence: private_only → proposed: licence: permits (card says MIT)');
    expect(screen.getByRole('note')).toHaveTextContent('An annotation can unlock a public push.');
  });

  it('formats the countdown', () => {
    const now = Date.parse('2026-10-07T00:00:00Z');
    expect(expiresIn('2026-10-07T00:10:00Z', now)).toBe('expires in 10 min');
    expect(expiresIn('2026-10-07T05:00:00Z', now)).toBe('expires in 5 h');
    expect(expiresIn('2026-10-06T23:00:00Z', now)).toBe('expired');
  });
});

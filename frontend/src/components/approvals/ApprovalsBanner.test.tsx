import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { mockFetch } from '@/test/fetchMock';

import { ApprovalsBanner } from './ApprovalsBanner';

const pending = {
  id: 'apr_1', action: 'secret_write', target: 'x', summary: 'Write the secret setting hf_token', payload: {},
  request_digest: 'abc', requested_by: 'agent:dataworks-mcp', status: 'pending',
  expires_at: new Date(Date.now() + 3600_000).toISOString(), decided_by: null, created_at: new Date().toISOString(),
};

describe('ApprovalsBanner', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders nothing when nothing is pending', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [] } });
    const { container } = render(<ApprovalsBanner />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('approve and reject each send one POST for that approval', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/approvals': { approvals: [pending] },
      'POST /api/v1/approvals/apr_1/approve': { ...pending, status: 'executed' },
      'POST /api/v1/approvals/apr_1/reject': { ...pending, status: 'rejected' },
    });
    render(<ApprovalsBanner />);
    expect(await screen.findByText(/1 agent request waiting/)).toBeInTheDocument();
    expect(screen.getByText(/agent:dataworks-mcp/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve this request' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/approve'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Reject this request' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/reject'))).toBe(true));
    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts).toEqual([
      { method: 'POST', path: '/api/v1/approvals/apr_1/approve', body: undefined },
      { method: 'POST', path: '/api/v1/approvals/apr_1/reject', body: { reason: 'Rejected by the operator.' } },
    ]);
  });
});

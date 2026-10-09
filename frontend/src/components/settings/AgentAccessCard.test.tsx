import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useAgentAccessStore } from '@/stores/agentAccessStore';
import { mockFetch } from '@/test/fetchMock';

import { AgentAccessCard } from './AgentAccessCard';

const ACTIONS = {
  hub_push: 'x', agent_label_rows: 'x', version_delete: 'x', millm_model_load: 'x', secret_write: 'x',
  source_annotate: 'x', gate_target_write: 'x',
};

const access = {
  mcp_public_url: 'http://mcp-dataworks.hitsai.local/mcp',
  mcp: { reachable: true, categories: ['core', 'datasets'], reason: null },
  gated_actions: ACTIONS,
  label_threshold: 5000,
  label_window_hours: 24,
  approval_ttl_hours: 24,
  activity: { window: 'last hour', identities: ['agent:dataworks-mcp'], sessions: 1, requests: 3 },
};

describe('AgentAccessCard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useAgentAccessStore.setState({ access: null, error: null });
  });

  it('shows every field read-only, the seven actions, the row rule and the activity', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/agent-access': access });
    render(<AgentAccessCard />);
    const url = await screen.findByLabelText('MCP server address');
    expect(url).toHaveValue('http://mcp-dataworks.hitsai.local/mcp');
    expect(url).toHaveAttribute('aria-readonly', 'true');
    expect(url).toHaveAttribute('readonly');
    expect(screen.getByLabelText('Tool categories')).toHaveValue('core, datasets');
    expect(screen.getAllByText('Set in the deployment.').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId('gated-actions').querySelectorAll('li')).toHaveLength(7);
    expect(screen.getByTestId('label-rule')).toHaveTextContent('5,000 rows per version per 24 h');
    expect(screen.getByTestId('agent-activity')).toHaveTextContent(
      '3 requests from 1 agent identity in 1 session, in the last hour.',
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('says why when the MCP server cannot be read', async () => {
    mockFetch({
      'GET /api/v1/agent-access': {
        ...access,
        mcp: { reachable: false, categories: null, reason: 'unreachable (ConnectError)' },
      },
    });
    render(<AgentAccessCard />);
    expect(await screen.findByTestId('mcp-unreachable')).toHaveTextContent('unreachable (ConnectError)');
    expect(screen.queryByLabelText('Tool categories')).toBeNull();
  });
});

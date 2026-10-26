// miDataworks stands alone (operator principle, 2026-10-07): an integration-only screen says WHY it
// cannot act, up front, instead of erroring or spinning. Integrations are detected at runtime —
// miStudio from GET /api/health, the generation endpoint's kind from its endpoint test — and an
// unknown answer never blocks (the API refuses on its own). Audit:
// 0xcc/reviews/standalone_audit_2026-10-07.md.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NewSteeredPairForm } from '@/components/generation/NewSteeredPairForm';
import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { getPanel } from '@/config/panels';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { useHealthStore } from '@/stores/healthStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { CLEAN_CHECKS, SET } from '@/test/fixtures009';
import type { Health } from '@/types/api';

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const withMiStudio = (configured: boolean): Health => ({
  ...(HEALTH as unknown as Health),
  dependencies: {
    ...(HEALTH as unknown as Health).dependencies,
    mistudio: configured
      ? { ok: true, reason: null, configured: true, url: 'http://mistudio.test/api/health' }
      : { ok: false, reason: 'not configured', configured: false },
  },
});

afterEach(() => {
  vi.unstubAllGlobals();
  useHealthStore.setState({ health: null, unreachable: false });
  useDetectorSetsStore.setState({ sets: [], current: null, checks: null, send: null, results: null, pendingApproval: null, error: null, notice: null });
  useGenerationStore.setState({ plan: null, planError: null, compareResult: null });
});

async function openCheckedSet() {
  const fetched = mockFetch({
    'GET /api/v1/detector-sets': { items: [SET], total: 1 },
    'GET /api/v1/detector-sets/dts_1': SET,
    'GET /api/v1/detector-sets/dts_1/results': () => ({ status: 404, json: { error: { code: 'report_not_found', message: 'none', details: {} } } }),
    'POST /api/v1/detector-sets/dts_1/checks': CLEAN_CHECKS,
  });
  render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
  fireEvent.click(await screen.findByTestId('set-card-humor-set'));
  await screen.findByTestId('set-detail');
  fireEvent.click(screen.getByRole('button', { name: 'Run checks' }));
  await screen.findByTestId('detector-checks');
  fireEvent.change(screen.getByLabelText('Hugging Face namespace'), { target: { value: 'someone' } });
  return fetched;
}

describe('Detector sets with no miStudio', () => {
  it('says why up front, and neither sends nor refreshes', async () => {
    useHealthStore.setState({ health: withMiStudio(false) });
    const { calls } = await openCheckedSet();
    expect(screen.getByTestId('mistudio-unconfigured')).toHaveTextContent('miStudio is not configured (MISTUDIO_BASE_URL)');
    expect(screen.getByTestId('mistudio-unconfigured')).toHaveTextContent('publish its versions to the Hugging Face Hub');
    const send = screen.getByRole('button', { name: 'Send to miStudio' });
    const refresh = screen.getByRole('button', { name: 'Refresh results' });
    expect(send).toBeDisabled();
    expect(refresh).toBeDisabled();
    expect(screen.getByTestId('send-blocked')).toHaveTextContent('Sending needs miStudio, which is not configured.');
    expect(screen.getByTestId('no-results')).toHaveTextContent('results come back only from a configured miStudio');
    fireEvent.click(send);
    fireEvent.click(refresh);
    expect(calls.filter((c) => c.path.endsWith('/send') || c.path.endsWith('/results/refresh'))).toHaveLength(0);
  });

  it('with miStudio configured the same set sends (the block follows health, not a constant)', async () => {
    useHealthStore.setState({ health: withMiStudio(true) });
    await openCheckedSet();
    expect(screen.queryByTestId('mistudio-unconfigured')).toBeNull();
    expect(screen.getByRole('button', { name: 'Send to miStudio' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Refresh results' })).toBeEnabled();
  });

  it('while health is unknown nothing is blocked (the API refuses on its own)', async () => {
    await openCheckedSet();
    expect(screen.queryByTestId('mistudio-unconfigured')).toBeNull();
    expect(screen.getByRole('button', { name: 'Send to miStudio' })).toBeEnabled();
  });
});

const VERSIONS = { items: [{ id: 'v1', dataset_id: 'd', dataset_name: 'pairs', target_type: 'dpo', number: 1, state: 'completed', is_head: true, superseded_by: null, parent_version_id: null, total_rows: 10, total_bytes: 1, warnings_count: 0 }], total: 1 };
const TEMPLATES = { items: [], total: 0, page: 1, limit: 50 };

function endpointTest(serverKind: string | null, status = 200) {
  return () => ({
    status,
    json: status === 200
      ? { role: 'generation', reachable: true, model_listed: true, protocol_ok: true, server_kind: serverKind, resident_model: null, lease_supported: false, lease_state: null, queue: null, error_code: null, message: 'ok' }
      : { error: { code: 'ROLE_UNCONFIGURED', message: 'no generation endpoint', details: {} } },
  });
}

describe('Steered pairs off miLLM', () => {
  it('a generic generation endpoint is detected at runtime and the form says steering needs miLLM', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/versions': VERSIONS,
      'GET /api/v1/generation-templates': TEMPLATES,
      'POST /api/v1/endpoint-roles/generation/test': endpointTest('openai_compatible'),
    });
    render(<NewSteeredPairForm onStarted={() => undefined} />);
    const notice = await screen.findByTestId('steering-unavailable');
    expect(notice).toHaveTextContent('Steered pairs need miLLM');
    expect(notice).toHaveTextContent('an OpenAI-compatible server');
    await waitFor(() => expect(screen.getByRole('option', { name: 'pairs v1' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Input version'), { target: { value: 'v1' } });
    expect(screen.getByRole('button', { name: 'Check the plan' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Build .* steered pairs$/ })).toBeDisabled();
    expect(calls.filter((c) => c.path === '/api/v1/endpoint-roles/generation/test' && c.method === 'POST')).toHaveLength(1);
    expect(calls.filter((c) => c.path.endsWith('/plan') || c.path.endsWith('/compare'))).toHaveLength(0);
  });

  it('miLLM, or an endpoint test that fails, leaves the form as it was', async () => {
    for (const answer of [endpointTest('millm'), endpointTest(null, 409)]) {
      mockFetch({
        'GET /api/v1/versions': VERSIONS,
        'GET /api/v1/generation-templates': TEMPLATES,
        'POST /api/v1/endpoint-roles/generation/test': answer,
        'POST /api/v1/steering-settings/compare': { one_axis: false, differing: [], differing_index: null, not_comparable: [], snapshots: [], message: 'Name a profile.', code: 'x' },
      });
      const { unmount } = render(<NewSteeredPairForm onStarted={() => undefined} />);
      await waitFor(() => expect(screen.getByRole('option', { name: 'pairs v1' })).toBeInTheDocument());
      fireEvent.change(screen.getByLabelText('Input version'), { target: { value: 'v1' } });
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByTestId('steering-unavailable')).toBeNull();
      expect(screen.getByRole('button', { name: 'Check the plan' })).toBeEnabled();
      unmount();
      vi.unstubAllGlobals();
    }
  });
});

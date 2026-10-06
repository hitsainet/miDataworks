import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useJobsStore } from '@/stores/jobsStore';
import { job, mockFetch } from '@/test/fetchMock';

import { ActiveOperations, ONE_MODEL_NOTICE } from './ActiveOperations';
import { FailedOperations } from './FailedOperations';
import { OperationsDrawer } from './OperationsDrawer';

describe('operations', () => {
  beforeEach(() => useJobsStore.setState({ active: [], failed: [], error: null, lastNotice: null }));
  afterEach(() => vi.unstubAllGlobals());

  it('cancel sends one POST for that job and refreshes', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/jobs/job_1/cancel': { job: job({ status: 'cancelling' }), detail: 'Cancellation requested.' },
      'GET /api/v1/jobs': { jobs: [] },
    });
    act(() => useJobsStore.setState({ active: [job() as never] }));
    render(<ActiveOperations />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel this job' }));
    await waitFor(() => expect(useJobsStore.getState().lastNotice).toBe('Cancellation requested.'));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ method: 'POST', path: '/api/v1/jobs/job_1/cancel', body: {} }]);
  });

  it('a queued job shows its reason and the one-model notice', () => {
    act(() =>
      useJobsStore.setState({
        active: [job({ status: 'queued', required_model_id: 'm2', queue_reason: 'Waiting: miLLM serves one model at a time, and job job_0 is using m1.' }) as never],
      }),
    );
    render(<ActiveOperations />);
    expect(screen.getByTestId('queue-reason')).toHaveTextContent('job_0 is using m1');
    expect(screen.getByText(ONE_MODEL_NOTICE)).toBeInTheDocument();
  });

  it('dismiss sends one POST for that failed job', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/jobs/job_9/dismiss': job({ id: 'job_9', status: 'failed' }),
      'GET /api/v1/jobs': { jobs: [] },
    });
    act(() => useJobsStore.setState({ failed: [job({ id: 'job_9', status: 'failed', error: 'worker died' }) as never] }));
    render(<FailedOperations />);
    expect(screen.getByText('worker died')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss this failure' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/jobs/job_9/dismiss')).toBe(true));
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('the drawer starts a self-test job with its duration', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/jobs/selftest': job({ status: 'queued' }),
      'GET /api/v1/jobs': { jobs: [] },
    });
    render(<OperationsDrawer open onClose={() => undefined} />);
    fireEvent.click(screen.getByRole('button', { name: 'Run a 10-second self-test job' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(calls.filter((c) => c.method === 'POST')).toEqual([
      { method: 'POST', path: '/api/v1/jobs/selftest', body: { duration_seconds: 10 } },
    ]);
  });

  it('a socket event moves the bar', () => {
    act(() => useJobsStore.setState({ active: [job() as never] }));
    act(() => useJobsStore.getState().applyEvent({ job_id: 'job_1', progress: 75 }));
    expect(useJobsStore.getState().active[0].progress).toBe(75);
  });

  it('an error state renders with a role the screen reader announces', async () => {
    mockFetch({ 'POST /api/v1/jobs/selftest': () => ({ status: 422, json: { error: { code: 'NO_IDENTITY', message: 'Set your name in Settings before starting work.', details: {} } } }), 'GET /api/v1/jobs': { jobs: [] } });
    render(<OperationsDrawer open onClose={() => undefined} />);
    fireEvent.click(screen.getByRole('button', { name: 'Run a 10-second self-test job' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Set your name in Settings');
  });
});

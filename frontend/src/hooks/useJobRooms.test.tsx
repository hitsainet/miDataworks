import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WebSocketProvider } from '@/contexts/WebSocketContext';
import type { SocketLike } from '@/contexts/WebSocketContext';
import { useJobsStore } from '@/stores/jobsStore';
import { job, mockFetch } from '@/test/fetchMock';

import { POLL_MS_DISCONNECTED, useJobRooms } from './useJobRooms';

function Probe() {
  useJobRooms();
  return null;
}

describe('useJobRooms', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useJobsStore.setState({ active: [], failed: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('polls the job records while the socket is disconnected', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/jobs': { jobs: [] } });
    const socket = { connected: false, on: () => socket, off: () => socket, emit: () => socket, disconnect: () => socket };
    render(
      <WebSocketProvider createSocket={() => socket as unknown as SocketLike}>
        <Probe />
      </WebSocketProvider>,
    );
    const initial = calls.length;
    await act(async () => {
      vi.advanceTimersByTime(POLL_MS_DISCONNECTED * 3 + 10);
    });
    // two lists per poll (active and failed)
    expect(calls.length - initial).toBeGreaterThanOrEqual(6);
  });

  it('subscribes each active job room once connected', async () => {
    mockFetch({ 'GET /api/v1/jobs': { jobs: [job()] } });
    const emitted: unknown[] = [];
    const handlers: Record<string, () => void> = {};
    const socket = {
      connected: false,
      on: (e: string, h: () => void) => {
        handlers[e] = h;
        return socket;
      },
      off: () => socket,
      emit: (e: string, d: unknown) => {
        emitted.push([e, d]);
        return socket;
      },
      disconnect: () => socket,
    };
    render(
      <WebSocketProvider createSocket={() => socket as unknown as SocketLike}>
        <Probe />
      </WebSocketProvider>,
    );
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    act(() => {
      socket.connected = true;
      handlers.connect();
    });
    expect(emitted).toContainEqual(['subscribe', { room: 'dataworks/selftest/job_1' }]);
  });
});

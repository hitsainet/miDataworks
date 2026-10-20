// The run room through the reference-counted socket, and the polling fallback (FTASKS 14.1).
import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WebSocketProvider } from '@/contexts/WebSocketContext';
import type { SocketLike } from '@/contexts/WebSocketContext';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { mockFetch } from '@/test/fetchMock';
import { run } from '@/test/fixtures005';

import { LABEL_RUN_POLL_MS, useLabelRun } from './useLabelRun';

function fakeSocket(connected: boolean) {
  const handlers = new Map<string, Set<(d: unknown) => void>>();
  const emitted: Array<[string, unknown]> = [];
  const socket = {
    connected,
    on: (e: string, h: (d: unknown) => void) => { if (!handlers.has(e)) handlers.set(e, new Set()); handlers.get(e)!.add(h); return socket; },
    off: (e: string, h?: (d: unknown) => void) => { if (h) handlers.get(e)?.delete(h); return socket; },
    emit: (e: string, d: unknown) => { emitted.push([e, d]); return socket; },
    disconnect: () => socket,
    fire: (e: string, d?: unknown) => handlers.get(e)?.forEach((h) => h(d)),
  };
  return { socket, emitted };
}

function Follow({ id }: { id: string }) {
  useLabelRun(id);
  return null;
}

describe('useLabelRun', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('subscribes to the run room and applies its events', () => {
    useLabelRunsStore.setState({ byId: { lr_1: run() }, live: {} });
    const { socket, emitted } = fakeSocket(true);
    render(<WebSocketProvider createSocket={() => socket as unknown as SocketLike}><Follow id="lr_1" /></WebSocketProvider>);
    act(() => socket.fire('connect'));
    expect(emitted).toContainEqual(['subscribe', { room: 'dataworks/label-runs/lr_1' }]);
    act(() => socket.fire('label_run:progress', { label_run_id: 'lr_1', rows_done: 7, rows_total: 9, rows_per_second: 3, eta_seconds: 1, counts: {} }));
    expect(useLabelRunsStore.getState().live.lr_1.rows_done).toBe(7);
    act(() => socket.fire('label_run:progress', { label_run_id: 'other', rows_done: 99 }));
    expect(useLabelRunsStore.getState().live.lr_1.rows_done).toBe(7);
  });

  it('polls the run while the socket is down', async () => {
    vi.useFakeTimers();
    const { calls } = mockFetch({ 'GET /api/v1/label-runs/lr_1': run() });
    useLabelRunsStore.setState({ byId: { lr_1: run() } });
    render(<Follow id="lr_1" />);
    await act(async () => { vi.advanceTimersByTime(LABEL_RUN_POLL_MS * 2 + 10); });
    expect(calls.filter((c) => c.path === '/api/v1/label-runs/lr_1').length).toBe(2);
  });
});

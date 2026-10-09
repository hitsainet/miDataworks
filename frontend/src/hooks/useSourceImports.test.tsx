// useSourceImports (001 FTDD section 7): a tracked import's room while connected, its job record
// every 5 s while not; events reach the store; a finished import's room is left.
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WebSocketProvider } from '@/contexts/WebSocketContext';
import type { SocketLike } from '@/contexts/WebSocketContext';
import { useSourcesStore } from '@/stores/sourcesStore';
import { job, mockFetch } from '@/test/fetchMock';

import { IMPORT_POLL_MS, useSourceImports } from './useSourceImports';

function Probe() {
  useSourceImports();
  return null;
}

const tracked = { jobId: 'job_3', label: 'org/data', status: 'running' as const, phase: null, progress: 0, sourceId: null, existing: false, error: null };

function fakeSocket(connected: boolean) {
  const emitted: unknown[] = [];
  const handlers: Record<string, Array<(d?: unknown) => void>> = {};
  const socket = {
    connected,
    on: (e: string, h: (d?: unknown) => void) => {
      (handlers[e] ??= []).push(h);
      return socket;
    },
    off: () => socket,
    emit: (e: string, d: unknown) => {
      emitted.push([e, d]);
      return socket;
    },
    disconnect: () => socket,
  };
  return { socket, emitted, handlers };
}

describe('useSourceImports', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSourcesStore.setState({ imports: { job_3: tracked } });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('polls the job record while the socket is disconnected', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/jobs/job_3': job({ id: 'job_3', status: 'running', progress: 20, message: 'downloading' }) });
    const { socket } = fakeSocket(false);
    render(<WebSocketProvider createSocket={() => socket as unknown as SocketLike}><Probe /></WebSocketProvider>);
    await act(async () => {
      vi.advanceTimersByTime(IMPORT_POLL_MS * 2 + 10);
    });
    expect(calls.filter((c) => c.path === '/api/v1/jobs/job_3').length).toBeGreaterThanOrEqual(2);
    expect(useSourcesStore.getState().imports.job_3.phase).toBe('downloading');
  });

  it('joins the import room once connected and applies its events', async () => {
    const { calls } = mockFetch({ 'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 } });
    const { socket, emitted, handlers } = fakeSocket(false);
    render(<WebSocketProvider createSocket={() => socket as unknown as SocketLike}><Probe /></WebSocketProvider>);
    act(() => {
      socket.connected = true;
      handlers.connect?.forEach((h) => h());
    });
    expect(emitted).toContainEqual(['subscribe', { room: 'dataworks/source-imports/job_3' }]);
    act(() => handlers['source_import:progress']?.forEach((h) => h({ job_id: 'job_3', phase: 'writing' })));
    expect(useSourcesStore.getState().imports.job_3.phase).toBe('writing');
    act(() => handlers['source_import:completed']?.forEach((h) => h({ job_id: 'job_3', source_id: 's1', existing: false })));
    expect(useSourcesStore.getState().imports.job_3.status).toBe('completed');
    expect(emitted).toContainEqual(['unsubscribe', { room: 'dataworks/source-imports/job_3' }]);
    expect(calls.some((c) => c.path.startsWith('/api/v1/sources'))).toBe(true);
  });
});

describe('the Datasets screen follows imports', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSourcesStore.setState({ imports: { job_3: tracked }, meta: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('polls a tracked import while the screen is open and the socket is down', async () => {
    const { DatasetsPanel } = await import('@/components/panels/DatasetsPanel');
    const { getPanel } = await import('@/config/panels');
    const { calls } = mockFetch({
      'GET /api/v1/jobs/job_3': job({ id: 'job_3', status: 'running', progress: 50 }),
      'GET /api/v1/sources': { items: [], total: 0, page: 1, limit: 100 },
      'GET /api/v1/datasets': { items: [], total: 0, page: 1, limit: 50 },
    });
    const { socket } = fakeSocket(false);
    render(<WebSocketProvider createSocket={() => socket as unknown as SocketLike}><DatasetsPanel panel={getPanel('datasets')} /></WebSocketProvider>);
    await act(async () => {
      vi.advanceTimersByTime(IMPORT_POLL_MS + 10);
    });
    expect(calls.some((c) => c.path === '/api/v1/jobs/job_3')).toBe(true);
  });
});

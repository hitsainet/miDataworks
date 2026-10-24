// Follow one label run: its Socket.IO room `dataworks/label-runs/{id}` while connected (through the
// reference-counted WebSocketContext), polling GET /label-runs/{id} every 5 s while not (ADR-008).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useLabelRunsStore } from '@/stores/labelRunsStore';

export const LABEL_RUN_POLL_MS = 5000;
const EVENTS = ['label_run:progress', 'label_run:completed', 'label_run:failed', 'label_run:cancelled'];
const LIVE = new Set(['queued', 'running']);

export function useLabelRun(runId: string | null): void {
  const socket = useOptionalWebSocket();
  const run = useLabelRunsStore((s) => (runId ? s.byId[runId] : undefined));
  const applyEvent = useLabelRunsStore((s) => s.applyEvent);
  const fetchRun = useLabelRunsStore((s) => s.fetchRun);
  const connected = socket?.isConnected ?? false;
  const live = Boolean(run && LIVE.has(run.state));

  useEffect(() => {
    if (!socket || !runId) return undefined;
    const handlers = EVENTS.map((event) => {
      const handler = (data: unknown) => {
        const payload = (data ?? {}) as Record<string, unknown>;
        if (payload.label_run_id === runId) applyEvent(runId, event, payload);
      };
      socket.on(event, handler);
      return [event, handler] as const;
    });
    socket.subscribe(`dataworks/label-runs/${runId}`);
    return () => {
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socket.unsubscribe(`dataworks/label-runs/${runId}`);
    };
  }, [socket, runId, applyEvent]);

  useEffect(() => {
    if (!runId || !live || connected) return undefined;
    const timer = setInterval(() => void fetchRun(runId), LABEL_RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [runId, live, connected, fetchRun]);
}

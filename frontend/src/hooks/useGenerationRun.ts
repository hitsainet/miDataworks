// Follow one generation run: its room `dataworks/generation-runs/{id}` while connected (through the
// reference-counted WebSocketContext), polling GET /generation-runs/{id} every 5 s while not
// (ADR-008).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useGenerationStore } from '@/stores/generationStore';

export const GENERATION_POLL_MS = 5000;
const EVENTS = ['generation_run:progress', 'generation_run:completed', 'generation_run:failed'];
const LIVE = new Set(['queued', 'running']);

export function useGenerationRun(runId: string | null): void {
  const socket = useOptionalWebSocket();
  const run = useGenerationStore((s) => (runId ? s.byId[runId] : undefined));
  const applyEvent = useGenerationStore((s) => s.applyEvent);
  const loadRun = useGenerationStore((s) => s.loadRun);
  const connected = socket?.isConnected ?? false;
  const live = Boolean(run && LIVE.has(run.state));

  useEffect(() => {
    if (!socket || !runId) return undefined;
    const handlers = EVENTS.map((event) => {
      const handler = (data: unknown) => {
        const payload = (data ?? {}) as Record<string, unknown>;
        if (payload.generation_run_id === runId) applyEvent(runId, event, payload);
      };
      socket.on(event, handler);
      return [event, handler] as const;
    });
    socket.subscribe(`dataworks/generation-runs/${runId}`);
    return () => {
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socket.unsubscribe(`dataworks/generation-runs/${runId}`);
    };
  }, [socket, runId, applyEvent]);

  useEffect(() => {
    if (!runId || !live || connected) return undefined;
    const timer = setInterval(() => void loadRun(runId), GENERATION_POLL_MS);
    return () => clearInterval(timer);
  }, [runId, live, connected, loadRun]);
}

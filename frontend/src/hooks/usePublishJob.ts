// Follow the Publish screen's current job: its Socket.IO room while connected, the job record by
// polling while not (ADR-008).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { usePublishStore } from '@/stores/publishStore';

export const PUBLISH_POLL_MS = 2000;
const ROOMS: Record<string, string> = {
  publish_build: 'publish-builds',
  publish_check: 'publish-checks',
  publish: 'publishes',
  publish_reverify: 'publish-reverifications',
  export: 'exports',
};

export function usePublishJob(): void {
  const socket = useOptionalWebSocket();
  const job = usePublishStore((s) => s.job);
  const applyJobEvent = usePublishStore((s) => s.applyJobEvent);
  const pollJob = usePublishStore((s) => s.pollJob);
  const live = job && !['completed', 'failed', 'cancelled'].includes(job.status);
  const connected = socket?.isConnected ?? false;
  const jobId = job?.jobId;
  const kind = job?.kind;

  useEffect(() => {
    if (!socket || !jobId || !kind) return undefined;
    const room = `dataworks/${ROOMS[kind]}/${jobId}`;
    const events = ['progress', 'completed', 'failed', 'cancelled'].map((e) => `${kind}:${e}`);
    const handlers = events.map((event) => {
      const handler = (data: unknown) => applyJobEvent(event, (data ?? {}) as Record<string, unknown>);
      socket.on(event, handler);
      return [event, handler] as const;
    });
    socket.subscribe(room);
    return () => {
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socket.unsubscribe(room);
    };
  }, [socket, jobId, kind, applyJobEvent]);

  useEffect(() => {
    if (!live || connected) return undefined;
    void pollJob();
    const timer = setInterval(() => void pollJob(), PUBLISH_POLL_MS);
    return () => clearInterval(timer);
  }, [live, connected, pollJob]);
}

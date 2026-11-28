// Follow one build: its Socket.IO room (dataworks/version-builds/{job}) while connected, and the job
// record by polling while not (ADR-008: stores fall back to polling).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useVersionsStore } from '@/stores/versionsStore';

export const BUILD_POLL_MS = 3000;
const EVENTS = ['version_build:progress', 'version_build:completed', 'version_build:failed'] as const;

export function useBuildRoom(): void {
  const socket = useOptionalWebSocket();
  const build = useVersionsStore((s) => s.build);
  const applyBuildEvent = useVersionsStore((s) => s.applyBuildEvent);
  const pollBuild = useVersionsStore((s) => s.pollBuild);
  const jobId = build?.jobId;
  const live = build && !['completed', 'failed', 'cancelled'].includes(build.status);
  const connected = socket?.isConnected ?? false;

  useEffect(() => {
    if (!socket || !jobId) return undefined;
    const room = `dataworks/version-builds/${jobId}`;
    const handlers = EVENTS.map((event) => {
      const handler = (data: unknown) => applyBuildEvent(event, (data ?? {}) as Record<string, unknown>);
      socket.on(event, handler);
      return [event, handler] as const;
    });
    socket.subscribe(room);
    return () => {
      handlers.forEach(([event, handler]) => socket.off(event, handler));
      socket.unsubscribe(room);
    };
  }, [socket, jobId, applyBuildEvent]);

  useEffect(() => {
    if (!live || connected) return undefined;
    void pollBuild();
    const timer = setInterval(() => void pollBuild(), BUILD_POLL_MS);
    return () => clearInterval(timer);
  }, [live, connected, pollBuild]);
}

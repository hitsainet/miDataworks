// Follow every tracked import: its Socket.IO room (dataworks/source-imports/{job}) while connected,
// the job record by polling every 5 s while not (001 FTDD section 7; ADR-008).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useSourcesStore } from '@/stores/sourcesStore';

export const IMPORT_POLL_MS = 5000;
export const IMPORT_EVENTS = ['source_import:progress', 'source_import:completed', 'source_import:failed', 'source_import:cancelled'] as const;
const TERMINAL = ['completed', 'failed', 'cancelled'];

export function useSourceImports(): void {
  const socket = useOptionalWebSocket();
  const imports = useSourcesStore((s) => s.imports);
  const applyImportEvent = useSourcesStore((s) => s.applyImportEvent);
  const pollImports = useSourcesStore((s) => s.pollImports);
  const live = Object.values(imports).filter((i) => !TERMINAL.includes(i.status)).map((i) => i.jobId);
  const liveKey = live.join(',');
  const connected = socket?.isConnected ?? false;

  useEffect(() => {
    if (!socket) return undefined;
    const handlers = IMPORT_EVENTS.map((event) => {
      const handler = (data: unknown) => applyImportEvent(event, (data ?? {}) as Record<string, unknown>);
      socket.on(event, handler);
      return [event, handler] as const;
    });
    return () => handlers.forEach(([event, handler]) => socket.off(event, handler));
  }, [socket, applyImportEvent]);

  useEffect(() => {
    if (!socket || !liveKey) return undefined;
    const rooms = liveKey.split(',').map((id) => `dataworks/source-imports/${id}`);
    rooms.forEach((room) => socket.subscribe(room));
    return () => rooms.forEach((room) => socket.unsubscribe(room));
  }, [socket, liveKey]);

  useEffect(() => {
    if (!liveKey || connected) return undefined;
    void pollImports();
    const timer = setInterval(() => void pollImports(), IMPORT_POLL_MS);
    return () => clearInterval(timer);
  }, [liveKey, connected, pollImports]);
}

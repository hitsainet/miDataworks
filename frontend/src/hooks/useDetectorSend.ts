// Follow one detector-set send: its room `dataworks/detector-sends/{id}` while connected, polling
// GET /api/v1/detector-sends/{id} every 5 s while not (FTDD 009 section 7.1).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';

export const DETECTOR_SEND_POLL_MS = 5000;
const LIVE = new Set(['queued', 'running']);

export function useDetectorSend(sendId: string | null, state: string | null): void {
  const socket = useOptionalWebSocket();
  const fetchSend = useDetectorSetsStore((s) => s.fetchSend);
  const connected = socket?.isConnected ?? false;
  const live = Boolean(sendId && state && LIVE.has(state));

  useEffect(() => {
    if (!socket || !sendId) return undefined;
    const room = `dataworks/detector-sends/${sendId}`;
    const handler = (data: unknown) => {
      const payload = (data ?? {}) as { send_id?: string };
      if (payload.send_id === sendId) void fetchSend(sendId);
    };
    socket.on('detector_send:step', handler);
    socket.on('detector_send:status', handler);
    socket.subscribe(room);
    return () => {
      socket.off('detector_send:step', handler);
      socket.off('detector_send:status', handler);
      socket.unsubscribe(room);
    };
  }, [socket, sendId, fetchSend]);

  useEffect(() => {
    if (!sendId || !live || connected) return undefined;
    const timer = setInterval(() => void fetchSend(sendId), DETECTOR_SEND_POLL_MS);
    return () => clearInterval(timer);
  }, [sendId, live, connected, fetchSend]);
}

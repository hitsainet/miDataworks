// Subscribe to every active job's room, and poll the job records when the socket is down
// (ADR-008: stores fall back to polling, as miStudio's do).
import { useEffect } from 'react';

import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useJobsStore } from '@/stores/jobsStore';

export const POLL_MS_DISCONNECTED = 3000;
export const POLL_MS_CONNECTED = 15000;

export function useJobRooms(): void {
  const socket = useOptionalWebSocket();
  const active = useJobsStore((s) => s.active);
  const fetchJobs = useJobsStore((s) => s.fetchJobs);
  const applyEvent = useJobsStore((s) => s.applyEvent);
  const connected = socket?.isConnected ?? false;
  const rooms = active.map((j) => j.room).join('|');

  useEffect(() => {
    void fetchJobs();
  }, [fetchJobs]);

  useEffect(() => {
    if (!socket) return undefined;
    const handler = (data: unknown) => applyEvent((data ?? {}) as Record<string, unknown>);
    socket.on('job:progress', handler);
    socket.on('job:status', handler);
    return () => {
      socket.off('job:progress', handler);
      socket.off('job:status', handler);
    };
  }, [socket, applyEvent]);

  useEffect(() => {
    if (!socket || !rooms) return undefined;
    const list = rooms.split('|');
    list.forEach((room) => socket.subscribe(room));
    return () => list.forEach((room) => socket.unsubscribe(room));
  }, [socket, rooms]);

  useEffect(() => {
    const timer = setInterval(() => void fetchJobs(), connected ? POLL_MS_CONNECTED : POLL_MS_DISCONNECTED);
    return () => clearInterval(timer);
  }, [connected, fetchJobs]);
}

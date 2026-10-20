// Follow one calibration compute job: its room `dataworks/calibration/{id}` while connected, polling
// GET /api/v1/jobs/{id} every 5 s while not (FTDD 006 section 7.2). On completion the store refetches
// the records (immutable, so the new one simply appears).
import { useEffect } from 'react';

import { api } from '@/api/client';
import { useOptionalWebSocket } from '@/contexts/WebSocketContext';
import { useCalibrationStore } from '@/stores/calibrationStore';

export const CALIBRATION_POLL_MS = 5000;
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export function useCalibrationJob(jobId: string | null): void {
  const socket = useOptionalWebSocket();
  const status = useCalibrationStore((s) => (jobId ? s.jobs[jobId] : undefined));
  const applyJobEvent = useCalibrationStore((s) => s.applyJobEvent);
  const connected = socket?.isConnected ?? false;
  const live = Boolean(jobId && status && !TERMINAL.has(status));

  useEffect(() => {
    if (!socket || !jobId) return undefined;
    const room = `dataworks/calibration/${jobId}`;
    const handler = (data: unknown) => {
      const payload = (data ?? {}) as { job_id?: string; status?: string };
      if (payload.job_id === jobId && payload.status) applyJobEvent(jobId, payload.status);
    };
    const done = (data: unknown) => {
      const payload = (data ?? {}) as { job_id?: string };
      if (payload.job_id === jobId) applyJobEvent(jobId, 'completed');
    };
    socket.on('job:status', handler);
    socket.on('calibration:completed', done);
    socket.subscribe(room);
    return () => {
      socket.off('job:status', handler);
      socket.off('calibration:completed', done);
      socket.unsubscribe(room);
    };
  }, [socket, jobId, applyJobEvent]);

  useEffect(() => {
    if (!jobId || !live || connected) return undefined;
    const timer = setInterval(() => {
      void api<{ status: string } | { job: { status: string } }>(`/api/v1/jobs/${encodeURIComponent(jobId)}`).then((body) => {
        const value = 'job' in body ? body.job.status : body.status;
        applyJobEvent(jobId, value);
      });
    }, CALIBRATION_POLL_MS);
    return () => clearInterval(timer);
  }, [jobId, live, connected, applyJobEvent]);
}

// Feature 006 review routes (FTDD 006 section 5.1).
import { api, json } from '@/api/client';
import type { AuditStatus, Decision, DecisionIn, QueueCreate, ReviewItem, ReviewQueue } from '@/types/review';

export const reviewApi = {
  queues: () => api<{ items: ReviewQueue[]; total: number }>('/api/v1/review-queues?limit=200'),
  queue: (id: string) => api<ReviewQueue>(`/api/v1/review-queues/${encodeURIComponent(id)}`),
  createQueue: (body: QueueCreate) => api<ReviewQueue>('/api/v1/review-queues', json('POST', body)),
  items: (id: string, page = 1, limit = 50) =>
    api<{ items: ReviewItem[]; total: number }>(`/api/v1/review-queues/${encodeURIComponent(id)}/items?page=${page}&limit=${limit}`),
  decide: (itemId: string, body: DecisionIn) => api<Decision>(`/api/v1/review-items/${encodeURIComponent(itemId)}/decisions`, json('POST', body)),
  history: (itemId: string) => api<Decision[]>(`/api/v1/review-items/${encodeURIComponent(itemId)}/decisions`),
  audit: (versionId: string) => api<AuditStatus>(`/api/v1/versions/${encodeURIComponent(versionId)}/audit`),
  drawAudit: (versionId: string, size: number) => api<AuditStatus>(`/api/v1/versions/${encodeURIComponent(versionId)}/audit`, json('POST', { size })),
};

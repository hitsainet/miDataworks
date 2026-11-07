// Purge (operator decision 2026-10-20). The routes refuse agents; the UI is the operator's.
import type { PurgeBatch, PurgeItem } from '@/types/purge';

import { api, json } from './client';

export const purgeApi = {
  /** What each purge would remove, or why it is refused. Deletes nothing. */
  plan: (items: PurgeItem[]) => api<PurgeBatch>('/api/v1/purge/plan', json('POST', { items })),
  /** Each item on its own; a blocked item never stops the others. */
  purge: (items: PurgeItem[], reason?: string) =>
    api<PurgeBatch>('/api/v1/purge', json('POST', { items, reason: reason || undefined })),
};

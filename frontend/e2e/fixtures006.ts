// Route answers for feature 006's screens (FTASKS 12.10).
import { QH, item, queue, record } from '../src/test/fixtures006';

export function api006(seen: Array<{ method: string; path: string; body: unknown }> = []): (path: string, method: string, url: URL, body: unknown) => unknown {
  const decided = new Set<string>();
  return (path, method, _url, body) => {
    seen.push({ method, path, body });
    if (path === '/api/v1/calibration-records') return { items: [record(), record({ id: 'cr_2', labeler_identity_hash: 'f'.repeat(64), labeler_identity: { model_id: 'deberta-v3' }, verdict: { verdict: 'fails', rule: 'default_c3', numbers: { compared: 0.66, threshold: 0.7 }, target_id: null } })], total: 2 };
    if (path === '/api/v1/calibration-sets') return { items: [], total: 0 };
    if (path === '/api/v1/calibration-targets') return { question_hash: QH, current: null, default_lower_bound: 0.7, history: [] };
    if (path === '/api/v1/review-queues' && method === 'GET') return { items: [queue({ items: 2, decided: decided.size }), queue({ id: 'rq_2', kind: 'external', origin_app: 'miforge', question: 'Admit this sample?' })], total: 2 };
    if (path === '/api/v1/review-queues/rq_1/items') return { items: [item(), item({ id: 'ri_2', position: 1, text: { text: 'Senate passes budget bill' } })], total: 2 };
    if (path.startsWith('/api/v1/review-items/') && path.endsWith('/decisions') && method === 'GET') return [];
    if (path.startsWith('/api/v1/review-items/') && method === 'POST') {
      const id = path.split('/')[4];
      decided.add(id);
      return { status: 201, body: { id: `rd_${id}`, item_id: id, queue_id: 'rq_1', row_key: 'e', decision: (body as { decision: string }).decision, override_label: null, reason: 'accepted the model label', decided_by: 'Ada', decided_by_origin: 'operator', model_output_visible: true, version_id: 'v1', created_at: '2026-10-07T10:00:00Z' } };
    }
    return undefined;
  };
}

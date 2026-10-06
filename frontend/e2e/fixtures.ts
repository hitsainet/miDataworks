import type { Page } from '@playwright/test';

export const HEALTH = {
  status: 'ok',
  dependencies: {
    postgres: { ok: true, reason: null },
    redis: { ok: true, reason: null },
    data_volume: { ok: true, reason: null, path: '/data/dataworks' },
    millm: { ok: true, reason: null, configured: true, url: 'http://millm.test/api/health' },
    mistudio: { ok: true, reason: null, configured: true, url: 'http://mistudio.test/api/health' },
  },
  resources: {
    cpu_percent: 23, memory_used_bytes: 31.4 * 2 ** 30, memory_total_bytes: 125.6 * 2 ** 30,
    disk_used_bytes: 1.2 * 2 ** 40, disk_total_bytes: 3.6 * 2 ** 40, disk_path: '/data/dataworks',
  },
};

const role = (r: string) => ({
  role: r, configured: false, protocol: null, base_url: null, model_id: null, api_key: null,
  has_api_key: false, inherit_from_judge: r === 'generation' || r === 'embeddings', use_mode: 'own', effective_role: null,
});

/** Answer every /api/ call; `failHealth` makes health fail to show the error state. */
export async function stubApi(page: Page, opts: { failHealth?: boolean; slowSettings?: boolean } = {}) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/health') return opts.failHealth ? route.abort() : json(HEALTH);
    if (path === '/api/v1/jobs') return json({ jobs: [] });
    if (path === '/api/v1/approvals') return json({ approvals: [] });
    if (path === '/api/v1/settings') {
      if (opts.slowSettings) await new Promise((r) => setTimeout(r, 1500));
      return json([{ key: 'operator_name', value: 'Ada', is_sensitive: false, is_set: true, category: 'identity', description: '', type: 'string' }]);
    }
    if (path === '/api/v1/endpoint-roles') return json(['classifier', 'judge', 'generation', 'embeddings'].map(role));
    if (path.startsWith('/api/ws/')) return route.abort();
    return json({ error: { code: 'NOT_FOUND', message: 'not stubbed', details: {} } }, 404);
  });
}

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

export const SOURCES_META = {
  kinds: ['hf', 'upload'],
  states: ['importing', 'ready', 'failed', 'cancelled', 'deleted'],
  annotation_kinds: ['terms', 'licence', 'detection_override'],
  redistribution: ['permits', 'private_only', 'forbids'],
  chat_formats: ['plain_text', 'role_content', 'from_value', 'unknown'],
  trl_types: ['language_modeling', 'prompt_only', 'prompt_completion', 'preference', 'none', 'undetected'],
  target_types: ['sft', 'dpo', 'kto', 'grpo', 'detector'],
  csv_defaults: { delimiter: ',', quote: '"', header: true, encoding: 'utf-8', type_mode: 'infer' },
  limits: { upload_max_bytes: 2147483648, import_confirm_bytes: 50000000000, preview_sample_rows: 100 },
};

export const AGENT_ACCESS = {
  mcp_public_url: 'http://mcp-dataworks.hitsai.local/mcp',
  mcp: { reachable: true, categories: ['calibration', 'core', 'curation', 'datasets', 'detector_sets', 'exports', 'generation', 'labeling', 'operators', 'review', 'settings'], reason: null },
  gated_actions: {
    hub_push: '', agent_label_rows: '', version_delete: '', millm_model_load: '', secret_write: '',
    source_annotate: '', gate_target_write: '',
  },
  label_threshold: 5000,
  label_window_hours: 24,
  approval_ttl_hours: 24,
  activity: { window: 'last hour', identities: ['agent:dataworks-mcp'], sessions: 1, requests: 12 },
};

const role = (r: string) => ({
  role: r, configured: false, protocol: null, base_url: null, model_id: null, api_key: null,
  has_api_key: false, inherit_from_judge: r === 'generation' || r === 'embeddings', use_mode: 'own', effective_role: null,
});

/** Answer every /api/ call; `failHealth` makes health fail to show the error state. */
export type Extra = (path: string, method: string, url: URL, body: unknown) => unknown | undefined;

/** Health with both siblings unset: miDataworks standalone (the 2026-10-07 principle). */
export const HEALTH_STANDALONE = {
  ...HEALTH,
  dependencies: {
    ...HEALTH.dependencies,
    millm: { ok: false, reason: 'not configured', configured: false },
    mistudio: { ok: false, reason: 'not configured', configured: false },
  },
};

export async function stubApi(page: Page, opts: { failHealth?: boolean; slowSettings?: boolean; extra?: Extra; health?: unknown } = {}) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/health') return opts.failHealth ? route.abort() : json(opts.health ?? HEALTH);
    if (path === '/api/v1/jobs') return json({ jobs: [] });
    if (path === '/api/v1/approvals') return json({ approvals: [] });
    if (path === '/api/v1/settings') {
      if (opts.slowSettings) await new Promise((r) => setTimeout(r, 1500));
      return json([{ key: 'operator_name', value: 'Ada', is_sensitive: false, is_set: true, category: 'identity', description: '', type: 'string' }]);
    }
    if (path === '/api/v1/endpoint-roles') return json(['classifier', 'judge', 'generation', 'embeddings'].map(role));
    if (path.startsWith('/api/ws/')) return route.abort();
    if (opts.extra) {
      const raw = route.request().postData();
      let parsed: unknown;
      try {
        parsed = raw ? JSON.parse(raw) : undefined;
      } catch {
        parsed = raw; // a multipart upload is not JSON
      }
      const answer = opts.extra(path, route.request().method(), url, parsed);
      if (answer !== undefined) {
        const { status, body } = answer as { status?: number; body?: unknown };
        return status !== undefined ? json(body, status) : json(answer);
      }
    }
    // Feature 001's Datasets screen asks for these on every visit; an empty estate by default.
    if (path === '/api/v1/sources/meta') return json(SOURCES_META);
    if (path === '/api/v1/sources') return json({ items: [], total: 0, page: 1, limit: 100 });
    // Feature 009's Detector sets screen: an empty estate by default.
    if (path === '/api/v1/detector-sets' && route.request().method() === 'GET') return json({ items: [], total: 0, page: 1, limit: 200 });
    if (path === '/api/v1/minimal-pair-chains' && route.request().method() === 'GET') return json({ items: [], total: 0, page: 1, limit: 50 });
    // Feature 010's Agent access card on the Settings screen.
    if (path === '/api/v1/agent-access') return json(AGENT_ACCESS);
    return json({ error: { code: 'NOT_FOUND', message: 'not stubbed', details: {} } }, 404);
  });
}

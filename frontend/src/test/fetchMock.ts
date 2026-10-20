// A fetch stub that answers by method and path, so components run their real stores and API
// client in tests (no hand-mocked store: the wiring under test is the real one).
import { vi } from 'vitest';

export type Route = (body: unknown) => { status?: number; json?: unknown };

export function mockFetch(routes: Record<string, Route | unknown>) {
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input.toString();
    const method = (init?.method ?? 'GET').toUpperCase();
    const raw = init?.body;
    const body =
      raw instanceof FormData
        ? { formData: Array.from(raw.entries()).map(([k, v]) => [k, typeof v === 'string' ? v : (v as File).name]) }
        : raw
          ? JSON.parse(String(raw))
          : undefined;
    calls.push({ method, path, body });
    const key = `${method} ${path.split('?')[0]}`;
    const route = routes[key] ?? routes[`${method} ${path}`];
    if (route === undefined) return new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: 'no route', details: {} } }), { status: 404 });
    const answer = typeof route === 'function' ? (route as Route)(body) : { json: route };
    return new Response(answer.json === undefined ? null : JSON.stringify(answer.json), { status: answer.status ?? 200 });
  });
  vi.stubGlobal('fetch', fn);
  return { fn, calls };
}

export const HEALTH = {
  status: 'ok',
  dependencies: {
    postgres: { ok: true, reason: null },
    redis: { ok: true, reason: null },
    data_volume: { ok: true, reason: null, path: '/data/dataworks' },
    millm: { ok: true, reason: null, configured: true, url: 'http://millm.test/api/health' },
    mistudio: { ok: false, reason: 'not configured', configured: false },
  },
  resources: { cpu_percent: 12, memory_used_bytes: 2 ** 30, memory_total_bytes: 2 ** 34, disk_used_bytes: 2 ** 35, disk_total_bytes: 2 ** 40, disk_path: '/data/dataworks' },
};

export const job = (overrides: Record<string, unknown> = {}) => ({
  id: 'job_1', kind: 'selftest', status: 'running', progress: 40, message: null, params: {}, result: null,
  error: null, started_by: 'Ada', started_by_origin: 'operator', required_model_id: null, queue_reason: null,
  heartbeat_at: null, cancel_requested_at: null, started_at: new Date().toISOString(), completed_at: null,
  dismissed_at: null, created_at: new Date().toISOString(), room: 'dataworks/selftest/job_1', ...overrides,
});

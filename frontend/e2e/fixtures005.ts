// Route answers for feature 005's screens (FTASKS 14.9).
import { PLAN, PROBE_LIST, PROBE_PLAN, RUBRIC, TEMPLATE, probeRun, run } from '../src/test/fixtures005';

export function api005(seen: Array<{ method: string; path: string; body: unknown }> = []): (path: string, method: string, url: URL, body: unknown) => unknown {
  return (path, method, url, body) => {
    seen.push({ method, path, body });
    if (path === '/api/v1/decision-templates') return [TEMPLATE];
    if (path === '/api/v1/rubrics' && method === 'GET') return [RUBRIC];
    if (path === '/api/v1/rubrics' && method === 'POST') {
      const b = body as { name: string; body: { style: string } };
      return { status: 201, body: { ...RUBRIC, id: 'rb_new', name: b.name, version: 1, ref: `${b.name}@1`, style: b.body.style, body: b.body } };
    }
    if (path === '/api/v1/rubrics/rb_1/export') return { format: 'midataworks.rubric/v1', name: RUBRIC.name, version: RUBRIC.version, body: RUBRIC.body };
    if (path === '/api/v1/labeling/probes') return PROBE_LIST;
    if (path === '/api/v1/label-runs/plan') {
      // 009: a probe-verdict plan answers with the probe and reproduction blocks.
      const request = JSON.parse(url.searchParams.get('request') ?? '{}') as { role?: string };
      return request.role === 'probe' ? PROBE_PLAN : PLAN;
    }
    if (path === '/api/v1/labeling/sample') {
      return {
        rows: [
          { row_key: 'k1', text: 'I told my wife she was drawing her eyebrows too high.', probability: 0.61, distribution: null, outcome: 'positive', verdict: null, rationale: null, latency_ms: 47, error: null },
          { row_key: 'k2', text: 'U.S. economy grows 1.2 percent', probability: 0.08, distribution: null, outcome: 'negative', verdict: null, rationale: null, latency_ms: 45, error: null },
        ],
        model: 'JEV-9B-decision', server_kind: 'millm', steering_state: 'unsteered (scoring mode)',
      };
    }
    if (path === '/api/v1/label-runs' && method === 'POST') {
      return { status: 201, body: (body as { role?: string } | null)?.role === 'probe' ? probeRun({ state: 'queued' }) : run({ state: 'queued' }) };
    }
    if (path === '/api/v1/label-runs' && method === 'GET') {
      return { items: [run(), run({ id: 'lr_2', state: 'completed', rows_done: 25000, pinned: false, endpoint_snapshot: { role: 'classifier', protocol: 'tei_classification', base_url: 'http://tei', model_id: 'deberta-v3', model_revision: 'e6535ca4', server_kind: 'tei' } })], total: 2, page: 1, limit: 50 };
    }
    if (path === '/api/v1/label-runs/lr_1/labels' || path === '/api/v1/label-runs/lr_2/labels') {
      return { items: Array.from({ length: 40 }, (_, i) => ({ label_run_id: 'lr_1', row_key: String(i).padStart(64, '0'), labeler_fingerprint: 'd', outcome: i % 3 === 0 ? 'excluded' : 'positive', parsed_value: {}, probability: (i % 20) / 20 + 0.01, distribution: null, raw_output: {}, rationale: null, steering_state: 'unsteered (scoring mode)', latency_ms: 40 + i, skip_reason: null, provisional: false, reused_from_run_id: null, chunk_index: 0, scored_at: '', started_by: 'Ada', started_by_origin: 'operator' })), total: 40, page: 1, limit: 500 };
    }
    if (path === '/api/v1/endpoint-roles/classifier/test') {
      return { role: 'classifier', reachable: true, model_listed: true, protocol_ok: true, server_kind: 'millm', resident_model: 'JEV-9B-decision', lease_supported: true, lease_state: 'free', queue: { queue_pending: 0 }, error_code: null, message: 'ok' };
    }
    if (path.startsWith('/api/v1/calibration-status')) return { status: 404, body: { error: { code: 'NOT_FOUND', message: 'no route', details: {} } } };
    return undefined;
  };
}

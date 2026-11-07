// Route answers for feature 009's Detector sets screen (FTASKS 9.9): a set is created, checked,
// sent (the send's steps advance on each read, as the fake miStudio would) and its results read.
import { CLEAN_CHECKS, RESULTS, SEND, SET } from '../src/test/fixtures009';

export function api009(seen: Array<{ method: string; path: string; body: unknown }> = []): (path: string, method: string, url: URL, body: unknown) => unknown {
  let created = false;
  let sent = false;
  let reads = 0;
  return (path, method, _url, body) => {
    seen.push({ method, path, body });
    if (path === '/api/v1/detector-sets' && method === 'GET') return { items: created ? [SET] : [], total: created ? 1 : 0 };
    if (path === '/api/v1/detector-sets' && method === 'POST') {
      created = true;
      return { status: 201, body: SET };
    }
    if (path === '/api/v1/detector-sets/dts_1' && method === 'GET') return { ...SET, sends: sent ? [SEND] : [] };
    if (path === '/api/v1/detector-sets/dts_1/results') return sent ? RESULTS : { status: 404, body: { error: { code: 'report_not_found', message: 'No results snapshot yet.', details: {} } } };
    if (path === '/api/v1/detector-sets/dts_1/checks') return CLEAN_CHECKS;
    if (path === '/api/v1/detector-sets/dts_1/send') {
      sent = true;
      return { status: 202, body: { send_id: 'dsn_1', job_id: 'job_1', state: 'queued' } };
    }
    if (path === '/api/v1/detector-sends/dsn_1') {
      reads += 1;
      return reads < 2 ? { ...SEND, state: 'running', run_request: null, steps: SEND.steps.map((s, i) => ({ ...s, state: i === 0 ? 'done' : 'running' })) } : SEND;
    }
    if (path === '/api/v1/detector-sets/dts_1/results/refresh') return RESULTS;
    return undefined;
  };
}

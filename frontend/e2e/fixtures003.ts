// Route answers for the Operators screen walk (task 11.10).
import { readFileSync } from 'node:fs';

import { ALLOWLIST, DROP_SHORT, LIST, PREVIEW, STATS } from '../src/test/fixtures003';

// Read, not imported: Playwright's ESM loader needs an import attribute for JSON.
const SUBSET: unknown = JSON.parse(readFileSync(new URL('../src/test/schemaSubset.json', import.meta.url), 'utf8'));

export function api003(): (path: string, method: string, url: URL, body: unknown) => unknown {
  return (path, method, _url, body) => {
    if (path === '/api/v1/operators' && method === 'GET') return LIST;
    if (path === '/api/v1/operators/schema-subset') return SUBSET;
    if (path === '/api/v1/operators/allowlist' && method === 'GET') return ALLOWLIST;
    if (path === '/api/v1/operators/allowlist' && method === 'POST') return { status: 201, body: { state: 'allowed' } };
    if (path === '/api/v1/operators/fx_drop_short/1') return DROP_SHORT;
    if (path === '/api/v1/operators/fx_drop_short/1/validate') {
      const params = (body as { params: Record<string, unknown> }).params;
      if (typeof params.min_len !== 'number') {
        return { status: 422, body: { error: { code: 'params_invalid', message: 'Fix the marked fields.', details: { errors: [{ pointer: '/min_len', message: `'${String(params.min_len)}' is not of type 'integer'` }] } } } };
      }
      return { valid: true };
    }
    if (path === '/api/v1/operators/fx_drop_short/1/preview') return { status: 200, body: { status: 'done', preview_id: 'p', result: PREVIEW } }; // stubApi reads a top-level status
    if (path === '/api/v1/operators/fx_drop_short/1/statistics') return { status: 200, body: { status: 'done', preview_id: 's', result: STATS } };
    return undefined;
  };
}

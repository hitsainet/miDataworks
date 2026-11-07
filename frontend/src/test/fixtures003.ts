// Feature 003 fixtures shared by Vitest and Playwright route stubs.
import type { AllowlistItem, OperatorEntry, StatisticsResult } from '@/types/operators';


export const DROP_SHORT: OperatorEntry = {
  name: 'fx_drop_short',
  version: '1',
  ref: 'fx_drop_short@1',
  state: 'allowed',
  origin: 'native',
  provider: 'native',
  error: null,
  manifest_hash: 'a'.repeat(64),
  entry_point: null,
  kind: 'filter',
  description: 'Drops rows whose text is shorter than a minimum length in characters.',
  provider_version: 'fixtures-1',
  scope: 'row',
  has_thresholds: true,
  queue: 'curation',
  endpoint_role: null,
  manifest: {
    name: 'fx_drop_short',
    version: '1',
    provider: 'native',
    provider_version: 'fixtures-1',
    kind: 'filter',
    scope: 'row',
    description: 'Drops rows whose text is shorter than a minimum length in characters.',
    input_columns: [{ name: 'text', type: 'string', required: true }],
    output_columns: [],
    params_schema: {
      type: 'object',
      properties: {
        min_len: { type: 'integer', minimum: 0, default: 10, title: 'Minimum length', 'x-unit': 'characters', 'x-hint': 'Rows shorter than this are dropped.' },
        column: { type: 'string', default: 'text', title: 'Text column', 'x-advanced': true },
      },
      required: ['min_len'],
      additionalProperties: false,
    },
    thresholds: [{ param: 'min_len', statistic: 'text_length', unit: 'characters', drop_when: 'below', pair_param: null }],
    resources: { queue: 'curation', endpoint_role: null, needs_lease: false, cpu_class: 'light', memory_class: 'light' },
    deterministic: true,
  },
};

export const DJ_LENGTH: OperatorEntry = {
  ...DROP_SHORT,
  name: 'dj_text_length_filter',
  version: 'dj1.6.0-1',
  ref: 'dj_text_length_filter@dj1.6.0-1',
  origin: 'catalogue:datajuicer',
  provider: 'datajuicer',
  description: 'Drops rows whose text length in characters falls outside a range.',
  provider_version: '1.6.0',
  manifest: undefined,
};

export const TAGGER: OperatorEntry = {
  ...DROP_SHORT,
  name: 'tagger',
  version: '1.0',
  ref: 'tagger@1.0',
  state: 'not_allowed',
  origin: 'plugin:acme-ops',
  provider: 'plugin:acme-ops',
  kind: null,
  description: null,
  has_thresholds: false,
  entry_point: ['acme-ops', '1.0', 'tagger'],
  error: 'Not imported: allow this entry point to see its operators.',
  manifest: undefined,
};

export const LIST = {
  items: [DROP_SHORT, DJ_LENGTH, TAGGER].map((e) => ({ ...e, manifest: undefined })),
  total: 3,
  page: 1,
  limit: 200,
  summary: { allowed: 2, not_allowed: 1, invalid_manifest: 0, failed_to_load: 0, duplicate: 0 },
};

export const ALLOWLIST: { items: AllowlistItem[] } = {
  items: [
    { distribution: 'acme-ops', distribution_version: '1.0', entry_point: 'tagger', value: 'acme_ops:operators', state: 'not_allowed', operators: [], history: [] },
  ],
};

const lengths = [3, 5, 12, 18, 25, 40, 44, 60, 72, 90];
export const STATS: StatisticsResult = {
  operator: 'fx_drop_short@1',
  sample_size: lengths.length,
  empty: false,
  thresholds: [
    {
      param: 'min_len',
      statistic: 'text_length',
      unit: 'characters',
      drop_when: 'below',
      pair_param: null,
      current: 10,
      constant: false,
      values: lengths.map((v, i) => ({ row_key: `k${i}`, occurrence: 0, value: v, excerpt: `row ${i} of length ${v}` })),
    },
  ],
};

export const PREVIEW = {
  operator: 'fx_drop_short@1',
  sample_size: 10,
  sample_requested: 300,
  seed: 0,
  empty: false,
  counts: { in: 10, kept: 8, changed: 0, dropped: 2, added: 0, split_assigned: 0 },
  examples: {
    kept: [{ row_key: 'k5', occurrence: 0, excerpt: 'a long enough row' }],
    changed: [],
    dropped: [
      { row_key: 'k0', occurrence: 0, reason_code: 'too_short', reason: 'text has 3 characters, below 10', statistic_name: 'text_length', statistic_value: 3, statistic_text: null, threshold: '{"comparator":"<","value":10}', excerpt: 'abc', kept_instead: null },
    ],
    added: [],
  },
  drops_total: 2,
};

// The native `sample` operator as GET /api/v1/operators/sample/1.0.0 serves it (novice walk
// 2026-10-19, finding 4). The params schema is the backend manifest's (operators/native/curation/
// sample.py); a test in noviceWalk.test.tsx renders it through the real SchemaForm.
export const SAMPLE_OP: OperatorEntry = {
  name: 'sample', version: '1.0.0', ref: 'sample@1.0.0', state: 'allowed', origin: 'native', provider: 'native', error: null,
  manifest_hash: 'b'.repeat(64), entry_point: null, kind: 'selector',
  description: 'Keeps a seeded random sample of the rows, by count or by share; every row left out is logged with its rank in the draw. The same seed draws the same rows.',
  provider_version: 'midataworks-004', scope: 'dataset', has_thresholds: false, queue: 'curation', endpoint_role: null,
  manifest: {
    name: 'sample', version: '1.0.0', provider: 'native', provider_version: 'midataworks-004', kind: 'selector', scope: 'dataset',
    description: 'Keeps a seeded random sample of the rows, by count or by share; every row left out is logged with its rank in the draw. The same seed draws the same rows.',
    input_columns: [], output_columns: [],
    params_schema: {
      type: 'object',
      properties: {
        mode: { type: 'string', enum: ['count', 'fraction'], default: 'count', title: 'Sample by', 'x-hint': 'count keeps a fixed number of rows; fraction keeps a share of them.' },
        count: { type: 'integer', minimum: 1, title: 'Rows to keep', 'x-unit': 'rows', 'x-hint': 'Used when sampling by count. An input with fewer rows is kept whole.' },
        fraction: { type: 'number', exclusiveMinimum: 0, maximum: 1, title: 'Share of rows to keep', 'x-unit': 'share, 0 to 1', 'x-hint': 'Used when sampling by fraction: 0.125 keeps one row in eight.' },
        seed: { type: 'integer', minimum: 0, maximum: 4294967295, title: 'Seed', 'x-advanced': true, 'x-hint': "Fixes which rows are drawn. Empty: the build's step seed, which the version records, so a rebuild with the same seed draws the same rows." },
      },
      required: [],
      additionalProperties: false,
    },
    thresholds: [],
    resources: { queue: 'curation', endpoint_role: null, needs_lease: false, cpu_class: 'medium', memory_class: 'medium' },
    deterministic: true,
  },
};

export const DEDUP_EXACT: OperatorEntry = {
  ...SAMPLE_OP, name: 'dedup_exact', ref: 'dedup_exact@1.0.0', kind: 'deduplicator',
  description: "Drops rows whose comparison key repeats an earlier row's, keeping the lowest row key; duplicates across splits are reported, never dropped.",
  manifest: { ...SAMPLE_OP.manifest!, name: 'dedup_exact', kind: 'deduplicator', params_schema: { type: 'object', properties: { collapse_whitespace: { type: 'boolean', default: false, title: 'Collapse inner whitespace' } }, required: [], additionalProperties: false } },
};

/** The other natives the Curate step suggests, as list rows (allowed, as a deployment ships them). */
const SUGGESTED = ['empty_content', 'length_band', 'dedup_minhash', 'normaliser', 'chat_json_parser', 'decontaminate'].map((name) => ({ ...DEDUP_EXACT, name, ref: `${name}@1.0.0`, kind: 'filter' as const, description: `${name} (native)` }));

/** The catalogue the Curate step reads: the suggested natives allowed, one entry point not allowed. */
export const CURATE_LIST = {
  items: [SAMPLE_OP, DEDUP_EXACT, ...SUGGESTED, TAGGER].map((e) => ({ ...e, manifest: undefined })),
  total: 9,
  page: 1,
  limit: 200,
  summary: { allowed: 8, not_allowed: 1, invalid_manifest: 0, failed_to_load: 0, duplicate: 0 },
};

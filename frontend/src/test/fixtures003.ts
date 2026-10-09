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

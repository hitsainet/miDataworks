// How the Environment card groups its rows (2026-10-28). The API's `group` says whether a setting
// can change while miDataworks runs (runtime, restart, approval_rule, environment); that is kept as
// the card's top level. Inside each, rows are grouped by the part of miDataworks they belong to,
// in a fixed order, and sorted by label. The rules match on the setting key; a key no rule names
// falls into "Other", so a new setting is never hidden, only unsorted until a rule names it.
import type { EnvironmentRow } from '@/types/api';

/** The sections, in the order the card shows them. */
export const SECTION_ORDER = [
  'Connections to other apps and services',
  'Imports and storage',
  'Operators and previews',
  'Curation and diversity',
  'Labeling and paid endpoints',
  'Generation',
  'Behaviours and rule sets',
  'Code rules',
  'Detector sets',
  'Publishing and versions',
  'Agents and approvals',
  'Security',
  'Jobs, logging and the deployment',
  'Other',
] as const;

export type Section = (typeof SECTION_ORDER)[number];

/** Exact keys, checked before the prefixes (they would otherwise match a broader prefix). */
const EXACT: Record<string, Section> = {
  agent_label_row_threshold: 'Agents and approvals',
  agent_label_window_hours: 'Agents and approvals',
  agent_request_retention_days: 'Agents and approvals',
  approval_ttl_hours: 'Agents and approvals',
  millm_lease_holder: 'Connections to other apps and services',
  millm_steering_supported: 'Generation',
  endpoint_http_timeout_seconds: 'Labeling and paid endpoints',
  health_probe_timeout_seconds: 'Connections to other apps and services',
  datajuicer_health_cache_s: 'Operators and previews',
  dj_num_proc: 'Operators and previews',
  preview_timeout_s: 'Operators and previews',
  keep_share_max_rows: 'Curation and diversity',
  keep_share_sample_rows: 'Curation and diversity',
  upload_max_bytes: 'Imports and storage',
  import_confirm_bytes: 'Imports and storage',
  data_dir: 'Imports and storage',
  parquet_row_group_rows: 'Imports and storage',
  orphan_sweep_age_minutes: 'Imports and storage',
  row_event_copy_chunk: 'Imports and storage',
  row_history_text_matches: 'Imports and storage',
  recipe_max_rule_sets: 'Behaviours and rule sets',
  reward_bundle_trl_version: 'Publishing and versions',
  ephemeral_secret_ttl_s: 'Security',
  internal_api_secret: 'Security',
  designer_handoff_key: 'Security',
  database_url: 'Connections to other apps and services',
  redis_url: 'Connections to other apps and services',
  celery_task_always_eager: 'Jobs, logging and the deployment',
  janitor_interval_seconds: 'Jobs, logging and the deployment',
  progress_heartbeat_seconds: 'Jobs, logging and the deployment',
  log_level: 'Jobs, logging and the deployment',
  environment: 'Jobs, logging and the deployment',
  app_build: 'Jobs, logging and the deployment',
};

/** Key prefixes, first match wins. */
const PREFIXES: Array<[string, Section]> = [
  ['code_rule', 'Code rules'],
  ['millm_', 'Connections to other apps and services'],
  ['mistudio_', 'Connections to other apps and services'],
  ['hf_', 'Connections to other apps and services'],
  ['mcp_', 'Connections to other apps and services'],
  ['source_', 'Imports and storage'],
  ['operator_', 'Operators and previews'],
  ['designer_', 'Operators and previews'],
  ['curation_', 'Curation and diversity'],
  ['diversity_', 'Curation and diversity'],
  ['classifier_', 'Labeling and paid endpoints'],
  ['label_', 'Labeling and paid endpoints'],
  ['judge_', 'Labeling and paid endpoints'],
  ['calibration_', 'Labeling and paid endpoints'],
  ['second_opinion_', 'Labeling and paid endpoints'],
  ['review_', 'Labeling and paid endpoints'],
  ['generation_', 'Generation'],
  ['behaviour_', 'Behaviours and rule sets'],
  ['rule_', 'Behaviours and rule sets'],
  ['detector_', 'Detector sets'],
  ['publish_', 'Publishing and versions'],
  ['version_', 'Publishing and versions'],
  ['settings_', 'Security'],
];

export function sectionOf(key: string): Section {
  if (key in EXACT) return EXACT[key];
  for (const [prefix, section] of PREFIXES) if (key.startsWith(prefix)) return section;
  // Any other address the deployment sets (public_api_url, runner_url, ...).
  if (key.endsWith('_url')) return 'Connections to other apps and services';
  return 'Other';
}

/** Rows grouped by section, sections in SECTION_ORDER, rows by label (case-insensitive). Empty
 * sections are left out. Every row lands in exactly one section. */
export function sectionRows<T extends Pick<EnvironmentRow, 'key' | 'label'>>(rows: T[]): Array<{ section: Section; rows: T[] }> {
  const by = new Map<Section, T[]>();
  for (const row of rows) {
    const section = sectionOf(row.key);
    by.set(section, [...(by.get(section) ?? []), row]);
  }
  return SECTION_ORDER.filter((s) => by.has(s)).map((section) => ({
    section,
    rows: [...(by.get(section) ?? [])].sort(
      (a, b) => a.label.localeCompare(b.label, 'en', { sensitivity: 'base' }) || a.key.localeCompare(b.key),
    ),
  }));
}


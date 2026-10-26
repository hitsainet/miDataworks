// Shared copy for feature 007's screens (FPRD 007 section 4.3): every number names its scale and
// sample, every pill carries text.
export const STATE_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  cancelled: 'Cancelled',
  failed: 'Failed',
};

export const REASON_LABEL: Record<string, string> = {
  steering_mismatch: 'Steering did not match',
  steering_unreported: 'Steering not reported',
  parse_failure: 'Did not parse',
  context_overflow: 'Too long for the model',
  profile_changed: 'Profile changed',
  pair_partner_discarded: 'Other side discarded',
  row_error: 'Request failed',
  empty_response: 'Empty answer',
};

export const FIGURE_LABEL: Record<string, string> = {
  distinct_1: 'distinct-1',
  distinct_2: 'distinct-2',
  embedding_spread: 'embedding spread',
  cluster_coverage: 'cluster coverage',
  largest_cluster_share: 'largest-cluster share',
};

export const fmt = (value: number | null | undefined, digits = 2): string =>
  value === null || value === undefined ? '—' : value.toFixed(digits);

/** "Steered on 1 feature" / "Profile humor" / "Unsteered" — what a snapshot asks for. */
export function settingText(kind: string, profile: string | null, features: number): string {
  if (kind === 'profile') return `Profile ${profile ?? '?'}`;
  if (kind === 'inline') return `Inline set, ${features} feature${features === 1 ? '' : 's'}`;
  return 'Unsteered';
}

/** 009's minimal-pair template is a respond template, but never the default for a run of 007's own:
 * it asks for a minimal edit, not an answer (operator decision 2026-10-07). */
export const MINIMAL_PAIR_TEMPLATE = 'minimal-pair-v1';

export function defaultRespondTemplate<T extends { kind: string; name: string }>(templates: T[]): T | undefined {
  return templates.find((t) => t.kind === 'respond' && t.name !== MINIMAL_PAIR_TEMPLATE);
}

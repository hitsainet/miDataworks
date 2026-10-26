// Number formatting for 009 (copy rule R-03.62: every number names its scale and sample).
import type { DetectorRole } from '@/types/detectorSets';

export const ROLE_WORDS: Record<DetectorRole, string> = {
  train: 'Training rows',
  id_test: 'In-distribution test',
  ood_eval: 'Out-of-distribution evaluation',
  calibration_negatives: 'Calibration negatives',
};

/** miStudio's role and distribution for each role (FR-009.4), as the role table shows it. */
export const MISTUDIO_VIEW: Record<DetectorRole, string> = {
  train: 'train',
  id_test: 'eval · in distribution',
  ood_eval: 'eval · out of distribution',
  calibration_negatives: 'calibration',
};

/** Shown when GET /api/health reports miStudio not configured (the standalone principle,
 * 2026-10-07): the set is still built, checked and published from here; only the send, the
 * results read-back and reward marks need miStudio, and the API refuses them naming it. */
export const MISTUDIO_UNCONFIGURED =
  'miStudio is not configured (MISTUDIO_BASE_URL), so this set cannot be sent or read back from here. ' +
  'Everything else works without it: publish its versions to the Hugging Face Hub from Publish and export, ' +
  'and import them into miStudio from the Hub.';

export const count = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString('en-US'));
export const pct = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`);
export const auroc = (x: number | null | undefined) => (x == null ? '—' : x.toFixed(4));

export function interval(ci: [number, number] | null | undefined): string {
  return ci ? `[${ci[0].toFixed(4)}, ${ci[1].toFixed(4)}]` : '';
}

/** "funny headlines firing 36.3% (n = 2,661)". */
export function firing(label: string, rate: number | null | undefined, n: number): string {
  return `${label} firing ${pct(rate)} (n = ${count(n)})`;
}

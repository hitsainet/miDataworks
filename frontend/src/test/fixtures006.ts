// Feature 006 fixtures shared by Vitest and Playwright.
import type { CalibrationRecord } from '@/types/calibration';
import type { ReviewItem, ReviewQueue } from '@/types/review';

export const QH = 'a'.repeat(64);

export function record(overrides: Partial<CalibrationRecord> = {}): CalibrationRecord {
  return {
    id: 'cr_1',
    calibration_set_id: 'cs_1',
    label_run_id: 'lr_1',
    question: 'Would most readers find this text funny?',
    question_hash: QH,
    labeler_identity: { model_id: 'JEV-9B-decision' },
    labeler_identity_hash: 'b'.repeat(64),
    labeler_fingerprint: 'c'.repeat(64),
    score_kind: 'probability',
    metrics: {
      score_kind: 'probability',
      n_rows: 20264,
      n_labeled: 6600,
      auroc: { value: 0.7533, ci_low: 0.7416, ci_high: 0.7654, n: 6600, n_pos: 2661, n_neg: 3939, resamples: 2000, dropped: 0 },
      ceiling: { value: 0.7419, ci_low: 0.7335, ci_high: 0.7498, draws: 20, n_mean: 5288 },
      comparison_auroc: { value: 0.7564, ci_low: 0.7457, ci_high: 0.7667 },
      paired: { value: 0.7778, ci_low: 0.7497, ci_high: 0.805, pairs: 945, groups: 745 },
      reference_diagnostic: { value: 0.9519, control: 0.8728, control_ci: [0.8613, 0.8833], n_pos: 2661, n_neg: 3939 },
      reliability: Array.from({ length: 10 }, (_, i) => ({ lo: i / 10, hi: (i + 1) / 10, n: i < 8 ? 300 - i * 20 : 4, positive_fraction: i < 8 ? i / 9 : 1, sparse: i >= 8 })),
      band_shares: { threshold_positive: 0.8, threshold_negative: 0.2, overall: { n: 6600, at_or_above: 0.012, at_or_below: 0.29, excluded: 0.698 }, by_class: {} },
      kappa: null,
      reasons: {},
    },
    metrics_sha256: 'd'.repeat(64),
    checks: [
      { check_id: 'row_alignment', check_version: 1, metric_id: 'auroc', result: 'pass', statistic: {}, rule: 'r', reason: null },
      { check_id: 'shortcut_comparison', check_version: 1, metric_id: 'auroc', result: 'not_applicable', statistic: {}, rule: 'r', reason: "004's held-out predictiveness (FR-004.29) is not served" },
      { check_id: 'negative_class_control', check_version: 1, metric_id: 'reference_diagnostic', result: 'fail', statistic: {}, rule: 'r', reason: 'negative rows also win 0.873 of the time' },
    ],
    verdict: { verdict: 'passes', rule: 'held_out_rater', numbers: { compared: 0.7564, threshold: 0.7334 }, target_id: null },
    warnings: [],
    settings: {},
    conformance: null,
    created_by: 'Ada',
    created_by_origin: 'operator',
    created_at: '2026-10-07T10:00:00Z',
    ...overrides,
  };
}

export function queue(overrides: Partial<ReviewQueue> = {}): ReviewQueue {
  return {
    id: 'rq_1', kind: 'label_review', version_id: 'v1', label_run_id: 'lr_1', question: 'Funny?', question_hash: QH,
    label_set: ['humorous', 'not_humorous'], show_model_output: true, sample_spec: null, origin_app: null, external_ref: null,
    state: 'open', created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-07T10:00:00Z', items: 2, decided: 0, ...overrides,
  };
}

export function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: 'ri_1', queue_id: 'rq_1', position: 0, row_key: 'e'.repeat(64), external_id: null, payload: null,
    model_snapshot: { label_run_id: 'lr_1', outcome: 'positive', label: 'humorous', probability: 0.81, rationale: null },
    model_output_hidden: false, stratum: 'humorous|at_or_above', text: { text: 'A headline about a cat mayor' }, latest_decision: null, ...overrides,
  };
}

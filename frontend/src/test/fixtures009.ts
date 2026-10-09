// Fixtures for feature 009's screen, shaped like the backend's responses.
import type { ChecksResult, DetectorSet, ResultsSnapshot, SendDetail } from '@/types/detectorSets';
import type { MinimalPairChain } from '@/types/minimalPairs';

const profile = (n: number, median: number) => ({
  version_id: 'v1', split: 'train', column: 'text', unit: 'chars', n,
  quantiles: { p05: median * 0.6, p25: median * 0.8, p50: median, p75: median * 1.2, p95: median * 1.5, p99: median * 1.7 },
  histogram: { edges: Array.from({ length: 31 }, (_, i) => i * 4), counts: Array.from({ length: 30 }, (_, i) => (i === 15 ? 10 : 1)) },
  finest_fpr: 1 / n,
});

export const SET: DetectorSet = {
  id: 'dts_1', name: 'humor-set', description: '', positive_meaning: 'Funny to most readers', archived: false,
  role_counts: { train: 1, id_test: 1, ood_eval: 1, calibration_negatives: 1 },
  last_send_state: null, last_send_id: null, last_rung: null, created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-07T00:00:00Z',
  monitored_ref: { kind: 'role', role_id: 'dsr_o' },
  roles: [
    { id: 'dsr_t', role: 'train', role_words: 'training rows', position: 0, version_id: 'v1', version_number: 2, version_state: 'completed', dataset_id: 'd1', dataset_name: 'humor-balanced', split: 'train', input_column: 'text', label_column: 'label', label_mapping: { humorous: 'positive', not_humorous: 'negative' }, pair_column: null, negatives_basis: null, display_name: '', view_name: 'humor-set - training rows' },
    { id: 'dsr_i', role: 'id_test', role_words: 'in-distribution test', position: 0, version_id: 'v1', version_number: 2, version_state: 'completed', dataset_id: 'd1', dataset_name: 'humor-balanced', split: 'test', input_column: 'text', label_column: 'label', label_mapping: { humorous: 'positive', not_humorous: 'negative' }, pair_column: null, negatives_basis: null, display_name: '', view_name: 'humor-set - in-distribution test' },
    { id: 'dsr_o', role: 'ood_eval', role_words: 'out-of-distribution evaluation', position: 0, version_id: 'v2', version_number: 1, version_state: 'completed', dataset_id: 'd2', dataset_name: 'humicroedit', split: 'test', input_column: 'text', label_column: 'label', label_mapping: { humorous: 'positive', not_humorous: 'negative' }, pair_column: 'pair_id', negatives_basis: null, display_name: '', view_name: 'humor-set - out-of-distribution evaluation' },
    { id: 'dsr_c', role: 'calibration_negatives', role_words: 'calibration negatives', position: 0, version_id: 'v3', version_number: 1, version_state: 'completed', dataset_id: 'd3', dataset_name: 'headline-negatives', split: 'train', input_column: 'text', label_column: 'label', label_mapping: { not_humorous: 'negative' }, pair_column: null, negatives_basis: { kind: 'assumed_negative' }, display_name: '', view_name: 'humor-set - calibration negatives' },
  ],
  profiles: {},
  sends: [],
};

const green = (code: string, title: string) => ({ code, title, outcome: 'green' as const, figure: null, reason: `${title}: fine.`, next_step: null, source: 'x', details: {} });

export const CHECKS: ChecksResult = {
  set_id: 'dts_1',
  outcomes: [
    green('D-1', 'Roles complete'), green('D-2', 'Label mappings valid'),
    { code: 'D-3', title: 'Shortcut audit', outcome: 'refused', figure: '0.910', reason: "Column 'format' predicts the label.", next_step: 'Balance the training rows (feature 004).', source: '004', details: {} },
    green('D-4', 'No leakage across roles'),
    { code: 'D-5', title: 'Calibration length matches', outcome: 'note', figure: '2.0% overlap', reason: 'Only 2.0% of lengths overlap.', next_step: 'Use headline-length negatives.', source: 'T-44', details: {} },
    green('D-6', 'Held-out roles not expanded'), green('D-7', 'Labelers have calibration records'),
    { code: 'D-8', title: 'Calibration negatives basis', outcome: 'note', figure: null, reason: 'Assumed negative.', next_step: null, source: 'FR-009.6', details: {} },
  ],
  send_allowed: false,
  first_refusal: null,
  label_values: { dsr_t: { humorous: 1104, not_humorous: 1104 }, dsr_i: { humorous: 122, not_humorous: 122 }, dsr_o: { humorous: 2661, not_humorous: 3939 }, dsr_c: { not_humorous: 2000 } },
  expected_counts: { dsr_t: { positive: 1104, negative: 1104, excluded: 0 }, dsr_i: { positive: 122, negative: 122, excluded: 0 }, dsr_o: { positive: 2661, negative: 3939, excluded: 0 }, dsr_c: { positive: 0, negative: 2000, excluded: 0 } },
  profiles: { dsr_c: profile(2000, 66) },
  monitored: { kind: 'role', profile: profile(6600, 67) },
  overlap_min: 0.5,
};
CHECKS.first_refusal = CHECKS.outcomes[2];

export const CLEAN_CHECKS: ChecksResult = {
  ...CHECKS,
  outcomes: CHECKS.outcomes.map((o) => (o.code === 'D-3' ? green('D-3', 'Shortcut audit') : o)),
  send_allowed: true,
  first_refusal: null,
};

export const SEND: SendDetail = {
  id: 'dsn_1', set_id: 'dts_1', state: 'completed', job_id: 'job_1', visibility: 'private', mistudio_base_url: 'http://mistudio.test',
  started_by: 'Ada', started_by_origin: 'operator', approval_id: null, created_at: '2026-10-07T00:00:00Z', completed_at: '2026-10-07T00:05:00Z', error: null,
  job_ids: ['job_1'], snapshot: { set: {}, roles: SET.roles, repositories: {} }, checks: [], notes: [],
  steps: [
    { step: 'publish', unit_key: 'v1', position: 0, role_ids: ['dsr_t'], state: 'done', publish_id: 'pub_1', repo_id: 'mistudio/humor-balanced-v2', commit: 'a'.repeat(40), mistudio_dataset_id: null, probe_dataset_id: null, expected_counts: null, registered_counts: null, error: null },
    { step: 'register', unit_key: 'dsr_t', position: 1, role_ids: ['dsr_t'], state: 'done', publish_id: null, repo_id: null, commit: null, mistudio_dataset_id: 'd-ms', probe_dataset_id: 'pmd_train', expected_counts: null, registered_counts: null, error: null },
  ],
  run_request: { train_dataset_id: 'pmd_train', eval_dataset_ids: ['pmd_test', 'pmd_ood'], calibration_dataset_id: 'pmd_cal' },
};

export const RESULTS: ResultsSnapshot = {
  id: 'dres_1', set_id: 'dts_1', send_id: 'dsn_1', read_at: '2026-10-07T01:00:00Z', read_by: 'Ada', runs: [],
  evaluations: [
    {
      probe_id: 'pm_935fc9088482', run_id: 'pmr_a2bcffdcd695', layer: 12, rule: 'rolling_mean_max', rule_params: { window: 4 }, selected: true,
      threshold: 27.61, target_fpr: 0.01, realised_fpr: 0.01, rung: 2, rung_language: 'detects on unseen tasks', rung_next_step: 'run the judge baseline on the same out-of-distribution sets',
      sets: [{ probe_dataset_id: 'pmd_ood', role: 'ood_eval', view_name: 'humor-set - out-of-distribution evaluation', auroc: 0.7375442903756163, ci: [0.7257708903315967, 0.7501896404192496], n_positive: 2661, n_negative: 3939, firing: { positives_firing: 0.3626, negatives_firing: 0.1127, unreachable: false, n_positive: 2661, n_negative: 3939 }, paired: { available: false, reason: 'not available: no accepted per-row source (T-47).' } }],
      not_from_this_set: [], caveats: { validation_caveat: null, threshold_transfer_caution: 'one threshold behaves very differently on each' }, judge_runs: [], reward: false,
    },
  ],
  training_reward: [],
  gone: { runs: [], probes: [] },
};

// Minimal pairs as a chain (operator decision 2026-10-07): one chain stopped at the judge (its
// model was not loaded), one completed with counts. Shapes as GET /minimal-pair-chains returns them.
const CHAIN_REQUEST = {
  input_version_id: 'v1', text_column: 'text', seed_splits: ['train'], sample_size: 6, seed: 5,
  respond_template_id: 'gt_mp', generator_setting: { kind: 'none' }, rubric_id: 'rb_1', judge_field: 'text',
  flip_from: 'yes', flip_to: 'no', judge_seed: 1, max_edit_chars: null, max_edit_words: 3,
};

export const CHAIN_STOPPED: MinimalPairChain = {
  id: 'mpc_1', state: 'failed', stage: 'judge', input_version_id: 'v1', request: CHAIN_REQUEST,
  stages: [
    { stage: 'generate', kind: 'generation_run', run_id: 'gr_1', job_id: null, version_id: null, state: 'completed' },
    { stage: 'scope', kind: 'version_build', run_id: null, job_id: 'job_s', version_id: 'v_scope', state: 'completed' },
    { stage: 'judge', kind: 'label_run', run_id: null, job_id: null, version_id: null, state: 'pending' },
    { stage: 'pair', kind: 'version_build', run_id: null, job_id: null, version_id: null, state: 'pending' },
  ],
  failed_stage: 'judge',
  error: { code: 'MODEL_NOT_LOADED', message: 'judge-model-9b is not loaded in miLLM (gen-model-7b is).', stage: 'judge', details: {} },
  counts: {}, pair_version_id: null, resumable: true, started_by: 'Ada', started_by_origin: 'operator',
  acting_by: 'Ada', acting_origin: 'operator', created_at: '2026-10-07T01:00:00Z', updated_at: '2026-10-07T01:05:00Z', completed_at: null,
};

export const CHAIN_DONE: MinimalPairChain = {
  ...CHAIN_STOPPED,
  id: 'mpc_1', state: 'completed', stage: 'done', failed_stage: null, error: null, resumable: false,
  stages: [
    CHAIN_STOPPED.stages[0], CHAIN_STOPPED.stages[1],
    { stage: 'judge', kind: 'label_run', run_id: 'lr_j', job_id: null, version_id: null, state: 'completed' },
    { stage: 'pair', kind: 'version_build', run_id: null, job_id: 'job_p', version_id: 'v_pairs', state: 'completed' },
  ],
  // every key _complete() writes (backend services/detector_sets/minimal_pair_chain.py)
  counts: {
    generated: 6,
    generation: { 'respond:generated': 6, pairs: 0 },
    scope_dropped: { edit_too_large: 1, no_edit: 1, not_in_run: 1, seed_without_counterpart: 2 },
    pairs_judged: 4,
    pairs_verified: 2,
    pairs_unverified: 2,
    unverified_by_reason: { flip_not_verified: 1, seed_not_flip_from: 1 },
  },
  pair_version_id: 'v_pairs', completed_at: '2026-10-07T01:09:00Z',
};

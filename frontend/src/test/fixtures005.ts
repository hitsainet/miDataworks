// Feature 005 fixtures shaped like the API's responses (backend/src/schemas/labeling.py).
import type { DecisionTemplate, LabelRun, Plan, ProbeFacts, ProbeList, Reproduction, Rubric } from '@/types/labeling';

export const TEMPLATE: DecisionTemplate = {
  id: 'dt_1', name: 'jev/noul-bare-v1', version: 1, ref: 'jev/noul-bare-v1@1', content_hash: 'a'.repeat(64),
  protocol: 'openai_scoring', variant: 'completions', bound_model_id: 'JEV-9B-decision', bound_model_revision: 'b63f651c',
  body: { label_set: ['false', 'true'], input_fields: ['text'], positive_class: 'true' }, used: false,
  created_by: 'miDataworks', created_by_origin: 'system', created_at: '2026-10-07T00:00:00Z',
};

export const RUBRIC: Rubric = {
  id: 'rb_1', name: 'humor/judge', version: 1, ref: 'humor/judge@1', content_hash: 'b'.repeat(64), style: 'pointwise',
  body: { input_fields: ['text'], allowed_verdicts: ['yes', 'no'] }, used: false,
  created_by: 'Ada', created_by_origin: 'operator', created_at: '2026-10-07T00:00:00Z',
};

export const PLAN: Plan = {
  rows_total: 25000, rows_reused: 0, rows_to_score: 25000, agent_window_rows: 0, approval_needed: false, threshold: 5000,
  labeler_identity: {}, labeler_identity_hash: 'c'.repeat(64), labeler_fingerprint: 'd'.repeat(64),
  server_kind: 'millm', resident_model: 'JEV-9B-decision', model_revision: 'b63f651c',
};

export const run = (overrides: Partial<LabelRun> = {}): LabelRun => ({
  id: 'lr_1', kind: 'classifier', state: 'running', input_version_id: 'v-1234567890',
  field_map: { text: 'text' },
  endpoint_snapshot: { role: 'classifier', protocol: 'openai_scoring', base_url: 'http://millm/v1', model_id: 'JEV-9B-decision', model_revision: 'b63f651ce8ed', server_kind: 'millm' },
  template_id: 'dt_1', rubric_id: null, template_ref: 'jev/noul-bare-v1@1', question: 'Is this text intended to be humorous?',
  positive_label: 'funny', negative_label: 'not funny', threshold_positive: 0.5, threshold_negative: 0.2, min_top_probability: null,
  label_set: ['false', 'true'], sampling: {}, structured_output: 'n/a', packing: 'single', batch_id: null, chunk_size: 200,
  row_filter: null, parent_run_ids: [], labeler_identity: {}, labeler_identity_hash: 'c'.repeat(64), labeler_fingerprint: 'd'.repeat(64),
  pinned: true, revision_reported: true, system_fingerprint: null, counts: { positive: 3, negative: 2, excluded: 1 },
  keep_share_estimate: { share: 0.47, lo: 0.42, hi: 0.52, n: 400 }, keep_share_actual: 0.472, length_correlation: null,
  rows_total: 25000, rows_reused: 0, rows_done: 6, agent_counted_rows: 0, approval_id: null, error: null,
  started_by: 'Ada', started_by_origin: 'operator', created_at: '2026-10-07T00:00:00Z', completed_at: null,
  job_ids: ['job_1'], current_job_id: 'job_1', room: 'dataworks/label-runs/lr_1', ...overrides,
});

// 009: probe-verdict runs. Shapes follow GET /api/v1/labeling/probes and the plan's `probe` and
// `reproduction` blocks exactly (null where the server sends null, absent where it omits).
export const PROBE_LIST: ProbeList = {
  items: [
    {
      probe_id: 'pr_5ac12236c0dd', name: 'high-stakes L16 mean', hf_id: 'meta-llama/Llama-3.1-8B-Instruct', layer: 16, scope: 'all',
      threshold: 11.914, threshold_revision: 2, window_thresholds: { all: 11.914, prompt: 7.9596, response: 16.9592 },
      rung: 2, rung_language: 'generalizes to unseen tasks; compared with a judge', armed: true, fits_resident_model: true,
    },
    {
      probe_id: 'pr_19c0458256f8', name: 'high-stakes L11 rolling', hf_id: 'LiquidAI/LFM2.5-1.2B-Instruct', layer: 11, scope: 'all',
      threshold: null, threshold_revision: 1, window_thresholds: {}, rung: null, rung_language: null, armed: false, fits_resident_model: false,
    },
  ],
  resident_model: 'meta-llama/Llama-3.1-8B-Instruct',
  millm_base_url: 'http://millm:8000',
};

export const REPRODUCTION_WILL_RUN: Reproduction = {
  state: 'will_run', role: 'test', view_name: 'anthropic_balanced', mistudio_auroc: 0.8911, mistudio_ci: [0.874, 0.907],
  n_rows: 1200, version_id: 'v1', split: 'test', snapshot_id: 'snap_1', set_id: 'ds_1', send_id: 'send_1',
  probe_dataset_id: 'pd_1', mistudio_probe_id: 'pm_3128ef6e8ae4',
};

export const PROBE_FACTS: ProbeFacts = {
  probe_id: 'pr_5ac12236c0dd', name: 'high-stakes L16 mean', hf_id: 'meta-llama/Llama-3.1-8B-Instruct', layer: 16, scope: 'all',
  window: 'all', window_bar: { threshold: 11.914, provisional: false }, threshold_revision: 2, rung: 2,
  rung_language: 'generalizes to unseen tasks; compared with a judge', armed: true,
  mistudio_probe_id: 'pm_3128ef6e8ae4', mistudio_run_id: 'pmr_416ce6b1ee63', load_dtype: 'bfloat16',
};

export const PROBE_PLAN: Plan = {
  ...PLAN,
  resident_model: 'meta-llama/Llama-3.1-8B-Instruct',
  model_revision: '0e9e39f2',
  probe: {
    ...PROBE_FACTS,
    preflight: {
      checked: true, row_key: 'a'.repeat(64), score: 12.31, threshold: 11.914, verdict: true, provisional: false, error: null,
      model: { hf_id: 'meta-llama/Llama-3.1-8B-Instruct', revision: '0e9e39f2', dtype: 'bfloat16', quantization: 'FP16' },
    },
  },
  reproduction: REPRODUCTION_WILL_RUN,
};

export const probeRun = (overrides: Partial<LabelRun> = {}): LabelRun => run({
  id: 'lr_p', kind: 'probe_verdict', state: 'completed', rows_done: 25000, template_id: null, template_ref: null, question: null,
  positive_label: null, negative_label: null, threshold_positive: null, threshold_negative: null, label_set: null, pinned: false,
  keep_share_estimate: null, keep_share_actual: null, counts: { positive: 4100, negative: 20900 },
  endpoint_snapshot: {
    role: 'probe', protocol: 'millm_probe_score', base_url: 'http://millm:8000', model_id: 'meta-llama/Llama-3.1-8B-Instruct',
    model_revision: '0e9e39f2', server_kind: 'millm',
    probe: PROBE_FACTS,
    reproduction: { ...REPRODUCTION_WILL_RUN, state: 'passed', millm_auroc: 0.8893, rows_scored: 1200, rows_dropped: 0, reason: null },
    pinned: false, unpinned_reason: 'miLLM does not report a model revision', millm_model: { hf_id: 'meta-llama/Llama-3.1-8B-Instruct' },
  },
  ...overrides,
});

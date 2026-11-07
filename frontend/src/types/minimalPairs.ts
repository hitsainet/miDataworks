// Feature 009 minimal pairs as a chain (operator decision 2026-10-07): the shapes the backend returns.
import type { SteeringSetting } from '@/types/generation';

export type ChainStageName = 'generate' | 'scope' | 'judge' | 'pair';
export type ChainState = 'running' | 'completed' | 'failed' | 'cancelled';

export interface ChainStage {
  stage: ChainStageName;
  kind: 'generation_run' | 'version_build' | 'label_run' | string;
  run_id: string | null;
  job_id: string | null;
  version_id: string | null;
  /** `pending` until the stage starts, then its run's or job's own state. */
  state: string;
}

export interface ChainError {
  code: string;
  message: string;
  stage: string;
  details?: Record<string, unknown>;
}

export interface ChainCounts {
  pairs_judged?: number;
  pairs_verified?: number;
  pairs_unverified?: number;
  unverified_by_reason?: Record<string, number>;
  scope_dropped?: Record<string, number>;
  generated?: number;
  generation?: Record<string, number>;
}

export interface MinimalPairChain {
  id: string;
  state: ChainState;
  stage: ChainStageName | 'done';
  input_version_id: string;
  request: Record<string, unknown>;
  stages: ChainStage[];
  failed_stage: ChainStageName | null;
  error: ChainError | null;
  counts: ChainCounts;
  pair_version_id: string | null;
  resumable: boolean;
  started_by: string;
  started_by_origin: string;
  acting_by: string;
  acting_origin: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ChainCreate {
  input_version_id: string;
  text_column: string;
  seed_splits: string[];
  sample_size: number;
  respond_template_id: string;
  rubric_id: string;
  flip_from: string;
  flip_to: string;
  seed?: number | null;
  generator_setting?: SteeringSetting;
  judge_field?: string | null;
  judge_seed?: number | null;
  max_edit_chars?: number | null;
  max_edit_words?: number | null;
}

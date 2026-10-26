// What a generation run is for, when it ran, who started it and what it produced, in words
// (operator request 2026-10-21). Pure helpers shared by the run tile and the open run.
import { formatDuration, formatEt } from '@/utils/format';
import type { GenerationRun, NamedVersion, RunBehaviour } from '@/types/generation';

import { REASON_LABEL } from './format';

export const MODE_NOUN: Record<string, string> = {
  standard: 'Standard run',
  steered_pairs: 'Steered pairs',
  minimal_pairs: 'Minimal pairs',
};

/** What each mode does, in one sentence a newcomer can read. */
export const MODE_EXPLAIN: Record<string, string> = {
  standard: 'Standard: each seed row is sent to the generator once per response asked for, and every answer becomes a new row.',
  steered_pairs: 'Steered pairs: each seed row is answered twice, once under each steering setting, so the two answers differ only by the steering.',
  minimal_pairs: 'Minimal pairs: each seed row is rewritten with the smallest edit that flips the target behaviour, giving a positive and a negative that differ only in that behaviour.',
};

/** What the rows are for, by target type; acronyms spelled out. */
export const TARGET_WORDS: Record<string, string> = {
  sft: 'supervised fine-tuning (SFT) rows',
  kto: 'Kahneman-Tversky optimization (KTO) rows',
  dpo: 'direct preference optimization (DPO) pairs',
  grpo_prompt: 'prompts for group relative policy optimization (GRPO)',
  detector: 'detector training rows',
};

/** "humor-jev9b v2"; an id prefix only when the backend sent no name. */
export function versionName(v: NamedVersion | null | undefined, fallbackId?: string): string {
  if (v) return `${v.dataset_name} v${v.number}`;
  return fallbackId ? `version ${fallbackId.slice(0, 8)}` : 'an unknown version';
}

/** The tile's title: what kind of run, from which dataset. */
export function runTitle(run: GenerationRun): string {
  return `${MODE_NOUN[run.mode] ?? run.mode} from ${versionName(run.input_version, run.input_version_id)}`;
}

/** The behaviour in a few words: the key when one is set, else what a positive means. */
export function behaviourLabel(b: RunBehaviour | null | undefined): string | null {
  if (!b) return null;
  return b.key ?? b.meaning ?? null;
}

export const NO_BEHAVIOUR = 'No behaviour set';
export const NO_BEHAVIOUR_HOW =
  'Set one by giving a behaviour key to a detector set built from this dataset (Detector sets), or to the rubric a minimal-pair chain judges with (Label runs → Rubrics).';

export function behaviourSourceText(b: RunBehaviour): string {
  if (b.source === 'run') return 'recorded on the run at start';
  return b.source === 'rubric' ? `from rubric ${b.source_name}` : `from detector set ${b.source_name}`;
}

const AGENT_PRODUCT: Record<string, string> = {
  'agent:dataworks-mcp': "miDataworks' MCP server",
  'agent:mistudio-mcp': "miStudio's MCP server",
  'agent:miforge-mcp': "miForge's MCP server",
};

/** "Sean McIntyre" for an operator; "an AI agent (through miStudio's MCP server)" for an agent. */
export function whoText(run: Pick<GenerationRun, 'started_by' | 'started_by_origin'>): string {
  if (run.started_by_origin !== 'agent') return run.started_by;
  const via = AGENT_PRODUCT[run.started_by] ?? run.started_by;
  return `an AI agent (through ${via})`;
}

const RUNNING = new Set(['running']);
const QUEUED = new Set(['queued']);

/** One line saying when: "Ran Oct 20, 2026, 3:47 AM ET for 2m 55s", "Running for 4m 2s since …",
 * "Queued for 3m", "Failed after 1m 5s on …". `now` is injectable for tests. */
export function whenLine(run: GenerationRun, now: number = Date.now()): string {
  const t = run.timing;
  const queuedAt = t?.queued_at ?? run.created_at;
  const started = t?.started_at ?? null;
  const finished = t?.finished_at ?? run.completed_at ?? null;
  if (QUEUED.has(run.state) && !started) {
    return `Queued ${formatEt(queuedAt) ?? 'at an unknown time'}, waiting ${formatDuration((now - Date.parse(queuedAt)) / 1000)}`;
  }
  if (RUNNING.has(run.state) || (QUEUED.has(run.state) && started)) {
    const since = started ?? queuedAt;
    return `Running for ${formatDuration((now - Date.parse(since)) / 1000)} · started ${formatEt(since) ?? 'at an unknown time'}`;
  }
  const total = t?.total_seconds ?? (started && finished ? (Date.parse(finished) - Date.parse(started)) / 1000 : null);
  const verb = run.state === 'failed' ? 'Failed after' : run.state === 'cancelled' ? 'Cancelled after' : 'Took';
  const took = total !== null && total !== undefined ? `${verb} ${formatDuration(total)}` : `${STATE_WORD[run.state] ?? run.state}, duration not recorded`;
  return `${took} · started ${formatEt(started ?? queuedAt) ?? 'at an unknown time'} · ended ${formatEt(finished) ?? 'time not recorded'}`;
}

const STATE_WORD: Record<string, string> = { completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' };

export function generatedCount(run: GenerationRun): number {
  return (run.counts['respond:generated'] ?? 0) + (run.counts['expand:generated'] ?? 0);
}

export function discardedCount(run: GenerationRun): number {
  return (run.counts['respond:discarded'] ?? 0) + (run.counts['expand:discarded'] ?? 0);
}

/** The commonest discard reasons, in words, most frequent first: "Did not parse 12 · Empty answer 3". */
export function topDiscardReasons(run: GenerationRun, n = 3): string | null {
  const reasons = Object.entries(run.counts)
    .filter(([k, v]) => k.startsWith('reason:') && v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n);
  if (!reasons.length) return null;
  return reasons.map(([k, v]) => `${REASON_LABEL[k.slice(7)] ?? k.slice(7)} ${v.toLocaleString()}`).join(' · ');
}

/** The seed source in words: "300 seed rows from humor v2 (train) × 1 response each". */
export function seedText(run: GenerationRun): string {
  const each = run.n_responses === 1 ? '1 response each' : `${run.n_responses} responses each`;
  return `${run.sample_size.toLocaleString()} seed rows from ${versionName(run.input_version, run.input_version_id)} (${run.seed_splits.join(', ')}) × ${each}`;
}

/** The template the run used: "respond-v1 v1" (respond) or the expand template. */
export function templateText(run: GenerationRun): string | null {
  const t = run.respond_template ?? run.expand_template;
  return t ? `${t.name} v${t.version}` : null;
}

export const REVISION_TOOLTIP =
  'The model server did not report which revision (commit) of the model it served, so the exact weights cannot be named and rebuilding this data later is less certain. miLLM reports a revision only when its model record has one; a model registered without one reads "not reported".';

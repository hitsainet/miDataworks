// Minimal pairs (009 US-9, FR-009.60 - 009.64; operator decision 2026-10-07). The generator is a
// chain of four stages that each record their own provenance: a generation run edits each seed, a
// build keeps the pairs, a judge run with a pinned rubric labels them, and a build keeps only the
// flips the judge verified. Each stage is shown with its run or version; a stopped chain names the
// stage and the reason, and resumes there.
import { useEffect, useState } from 'react';

import { useGenerationStore } from '@/stores/generationStore';
import { useMinimalPairsStore } from '@/stores/minimalPairsStore';
import type { ChainStage, MinimalPairChain } from '@/types/minimalPairs';

const input =
  'rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';
const primary = 'rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500';
const secondary = 'rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';

export const STAGE_WORDS: Record<string, string> = {
  generate: 'Generate one minimal edit per seed',
  scope: 'Keep each edit within the cap, with its seed',
  judge: 'Judge each seed and edit (pinned rubric)',
  pair: 'Keep only the verified flips',
};

const REASON_WORDS: Record<string, string> = {
  flip_not_verified: 'the judge did not read the edit as the target verdict',
  seed_not_flip_from: 'the judge did not read the seed as the starting verdict',
  judge_missing: 'the judge gave no verdict',
  judge_provisional: 'the verdict was provisional',
};

function stagePill(stage: ChainStage, chain: MinimalPairChain): { text: string; tone: string } {
  if (chain.failed_stage === stage.stage) return { text: 'Stopped here', tone: 'border-red-400 text-red-700 dark:text-red-300' };
  if (stage.state === 'completed') return { text: 'Done', tone: 'border-emerald-400 text-emerald-700 dark:text-emerald-300' };
  if (stage.state === 'pending') return { text: 'Not started', tone: 'border-slate-300 text-slate-500 dark:text-slate-400' };
  return { text: stage.state.charAt(0).toUpperCase() + stage.state.slice(1), tone: 'border-indigo-400 text-indigo-700 dark:text-indigo-300' };
}

function ref(stage: ChainStage): string {
  const parts = [];
  if (stage.run_id) parts.push(`run ${stage.run_id}`);
  if (stage.version_id) parts.push(`version ${stage.version_id}`);
  else if (stage.job_id) parts.push(`job ${stage.job_id}`);
  return parts.join(' · ');
}

export function ChainCard({ chain, onResume, onCancel }: { chain: MinimalPairChain; onResume: () => void; onCancel: () => void }) {
  const counts = chain.counts ?? {};
  return (
    <article className="rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-4" data-testid={`chain-${chain.id}`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold">{`${String(chain.request.flip_from)} → ${String(chain.request.flip_to)}`}</h4>
          <p className="text-xs text-slate-500 dark:text-slate-400">{`${chain.id} · ${chain.state} · started by ${chain.started_by}`}</p>
        </div>
        <div className="flex gap-2">
          {chain.resumable && <button type="button" className={secondary} onClick={onResume}>Resume</button>}
          {chain.state === 'running' && <button type="button" className={secondary} onClick={onCancel}>Cancel</button>}
        </div>
      </div>
      <ol className="space-y-1" data-testid="chain-stages">
        {chain.stages.map((stage, i) => {
          const pill = stagePill(stage, chain);
          return (
            <li key={stage.stage} className="flex flex-wrap items-center gap-2 text-sm" data-testid={`stage-${stage.stage}`}>
              <span className="w-5 text-xs text-slate-500 dark:text-slate-400">{i + 1}.</span>
              <span className="min-w-0 flex-1">{STAGE_WORDS[stage.stage] ?? stage.stage}</span>
              <span className={`rounded-full border px-2 py-0.5 text-xs ${pill.tone}`}>{pill.text}</span>
              {ref(stage) && <span className="w-full pl-7 text-xs text-slate-500 dark:text-slate-400 break-all">{ref(stage)}</span>}
            </li>
          );
        })}
      </ol>
      {chain.error && (
        <p role="alert" className="mt-2 rounded-md border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm" data-testid="chain-error">
          {`Stopped at “${STAGE_WORDS[chain.error.stage] ?? chain.error.stage}”: ${chain.error.message} (${chain.error.code})`}
        </p>
      )}
      {chain.state === 'completed' && (
        <div className="mt-2 text-sm" data-testid="chain-counts">
          <p>{`${counts.pairs_verified ?? 0} of ${counts.pairs_judged ?? 0} judged pairs verified; version ${chain.pair_version_id}.`}</p>
          {Object.entries(counts.unverified_by_reason ?? {}).map(([reason, n]) => (
            <p key={reason} className="text-xs text-slate-500 dark:text-slate-400">{`${n} dropped: ${REASON_WORDS[reason] ?? reason}`}</p>
          ))}
        </div>
      )}
    </article>
  );
}

function NewChainForm({ onDone }: { onDone: () => void }) {
  const startChain = useMinimalPairsStore((s) => s.startChain);
  const refusal = useMinimalPairsStore((s) => s.refusal);
  const templates = useGenerationStore((s) => s.templates);
  const loadTemplates = useGenerationStore((s) => s.loadTemplates);
  const [versionId, setVersionId] = useState('');
  const [column, setColumn] = useState('text');
  const [splits, setSplits] = useState('train');
  const [sample, setSample] = useState(100);
  // The operator's pick; until there is one, the built-in minimal-pair template (derived, so it
  // appears as soon as the templates load without an effect writing state).
  const [templateChoice, setTemplate] = useState('');
  const [rubric, setRubric] = useState('');
  const [flipFrom, setFlipFrom] = useState('yes');
  const [flipTo, setFlipTo] = useState('no');
  const [maxWords, setMaxWords] = useState('');
  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);
  const respond = templates.filter((t) => t.kind === 'respond');
  const template = templateChoice || (respond.find((t) => t.name === 'minimal-pair-v1')?.id ?? '');
  return (
    <section className="mb-4 rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-4" data-testid="new-chain-form">
      <h4 className="mb-2 text-sm font-semibold">New minimal pairs</h4>
      <div className="grid gap-2 sm:grid-cols-2">
        <input aria-label="Seed version ID" placeholder="Version ID" className={input} value={versionId} onChange={(e) => setVersionId(e.target.value)} />
        <input aria-label="Text column" placeholder="Text column" className={input} value={column} onChange={(e) => setColumn(e.target.value)} />
        <input aria-label="Seed splits" placeholder="Seed splits (comma-separated, never held out)" className={input} value={splits} onChange={(e) => setSplits(e.target.value)} />
        <input aria-label="Seed rows" type="number" min={1} className={input} value={sample} onChange={(e) => setSample(Number(e.target.value))} />
        <select aria-label="Edit template" className={input} value={template} onChange={(e) => setTemplate(e.target.value)}>
          <option value="">Choose a respond template</option>
          {respond.map((t) => <option key={t.id} value={t.id}>{t.ref}</option>)}
        </select>
        <input aria-label="Judge rubric ID" placeholder="Judge rubric ID (pinned)" className={input} value={rubric} onChange={(e) => setRubric(e.target.value)} />
        <input aria-label="Seed verdict" placeholder="The judge's verdict on the seed" className={input} value={flipFrom} onChange={(e) => setFlipFrom(e.target.value)} />
        <input aria-label="Edit verdict" placeholder="The judge's verdict on the edit" className={input} value={flipTo} onChange={(e) => setFlipTo(e.target.value)} />
        <input aria-label="Largest edit in words" placeholder="Largest edit in words (optional)" type="number" min={1} className={input} value={maxWords} onChange={(e) => setMaxWords(e.target.value)} />
      </div>
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">The judge must be a different model from the generator, and seeds never come from a held-out split; either is refused before anything runs.</p>
      {refusal && <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300" data-testid="chain-refusal">{`${refusal.message} (${refusal.code})`}</p>}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className={primary}
          disabled={!versionId || !template || !rubric}
          onClick={async () => {
            const started = await startChain({
              input_version_id: versionId,
              text_column: column,
              seed_splits: splits.split(',').map((s) => s.trim()).filter(Boolean),
              sample_size: sample,
              respond_template_id: template,
              rubric_id: rubric,
              flip_from: flipFrom,
              flip_to: flipTo,
              max_edit_words: maxWords ? Number(maxWords) : null,
            });
            if (started) onDone();
          }}
        >
          Start minimal pairs
        </button>
        <button type="button" className={secondary} onClick={onDone}>Close</button>
      </div>
    </section>
  );
}

/** Running chains are re-read on this interval; the server moves them between stages. */
export const POLL_MS = 5000;

export function MinimalPairsSection() {
  const chains = useMinimalPairsStore((s) => s.chains);
  const error = useMinimalPairsStore((s) => s.error);
  const loadChains = useMinimalPairsStore((s) => s.loadChains);
  const resumeChain = useMinimalPairsStore((s) => s.resumeChain);
  const cancelChain = useMinimalPairsStore((s) => s.cancelChain);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    void loadChains();
  }, [loadChains]);
  const running = chains.some((c) => c.state === 'running');
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => void loadChains(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, loadChains]);
  return (
    <section className="mt-6" data-testid="minimal-pairs">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">Minimal pairs</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">Each seed gets one minimal edit that flips the concept; a judge keeps only the flips it reads both ways. Pairs share a pair ID.</p>
        </div>
        <button type="button" className={primary} onClick={() => setCreating(true)}>New minimal pairs</button>
      </div>
      {error && <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
      {creating && <NewChainForm onDone={() => setCreating(false)} />}
      {chains.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-chains">No minimal pairs yet.</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {chains.map((c) => (
            <ChainCard key={c.id} chain={c} onResume={() => void resumeChain(c.id)} onCancel={() => void cancelChain(c.id)} />
          ))}
        </div>
      )}
    </section>
  );
}

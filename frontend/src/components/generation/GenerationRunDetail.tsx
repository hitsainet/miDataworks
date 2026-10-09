// One run: counts by outcome, generator and judge identities side by side, discards, pairs, and the
// next steps (build candidate version C; audit with generation strata).
import { useEffect, useState } from 'react';

import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { useGenerationRun } from '@/hooks/useGenerationRun';
import { useGenerationStore } from '@/stores/generationStore';
import type { GenerationRun } from '@/types/generation';

import { DiscardList } from './DiscardList';
import { PairBrowser } from './PairBrowser';
import { REASON_LABEL, STATE_LABEL, settingText } from './format';

export function GenerationRunDetail({ run }: { run: GenerationRun }) {
  useGenerationRun(run.id);
  const records = useGenerationStore((s) => s.records[run.id]) ?? [];
  const pairs = useGenerationStore((s) => s.pairs[run.id]) ?? [];
  const loadRecords = useGenerationStore((s) => s.loadRecords);
  const loadPairs = useGenerationStore((s) => s.loadPairs);
  const cancel = useGenerationStore((s) => s.cancel);
  const resume = useGenerationStore((s) => s.resume);
  const buildCandidate = useGenerationStore((s) => s.buildCandidate);
  const [building, setBuilding] = useState<string | null>(null);

  useEffect(() => {
    void loadRecords(run.id);
    if (run.mode === 'steered_pairs') void loadPairs(run.id);
  }, [run.id, run.mode, run.state, loadRecords, loadPairs]);

  const reasons = Object.entries(run.counts).filter(([k]) => k.startsWith('reason:'));
  return (
    <div className="space-y-4 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4" data-testid="generation-run-detail">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="default">{STATE_LABEL[run.state] ?? run.state}</Badge>
        <span className="text-xs text-slate-500 dark:text-slate-400">started by {run.started_by} ({run.started_by_origin}) · {run.engine_path} path</span>
      </div>
      {run.error && (
        <div role="alert" className="rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm">
          {run.error.message}
        </div>
      )}
      <div className="grid sm:grid-cols-2 gap-3 text-xs" data-testid="identities">
        <div>
          <div className="text-slate-500 dark:text-slate-400">Generator</div>
          {run.generator_identities.map((g) => (
            <div key={`${g.model_id}${g.set_hash}`} className="font-mono text-cyan-600 dark:text-cyan-400">{g.model_id} · {g.revision.slice(0, 12)} · {g.set_hash.slice(0, 15)}</div>
          ))}
        </div>
        <div>
          <div className="text-slate-500 dark:text-slate-400">Judge</div>
          <div className="font-mono">{run.judge_identity ? `${run.judge_identity.model_id} · ${run.judge_identity.revision.slice(0, 12)}` : 'not configured at start'}</div>
        </div>
      </div>
      <ul className="text-xs text-slate-600 dark:text-slate-300">
        {run.snapshots.filter((s) => s.side !== 'generator' || run.mode === 'standard').map((s) => (
          <li key={s.side}>Side {s.side}: {settingText(s.kind, s.profile_name, s.features.length)}{s.set_hash ? ` · ${s.set_hash.slice(0, 15)}` : ''}</li>
        ))}
      </ul>
      {reasons.length > 0 && (
        <p className="text-xs text-slate-600 dark:text-slate-300">
          Discarded: {reasons.map(([k, v]) => `${REASON_LABEL[k.slice(7)] ?? k.slice(7)} ${v}`).join(' · ')}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {run.current_job_id && <Button variant="danger" size="sm" onClick={() => void cancel(run.id)}>Cancel run</Button>}
        {run.resumable && <Button variant="secondary" size="sm" onClick={() => void resume(run.id)}>Resume run</Button>}
        {run.state === 'completed' && (
          <Button size="sm" onClick={async () => setBuilding(await buildCandidate(run.id))}>Build candidate version</Button>
        )}
      </div>
      {building && <p className="text-xs text-slate-500 dark:text-slate-400">Building the candidate version (job {building}).</p>}
      {run.mode === 'steered_pairs' && <PairBrowser pairs={pairs} />}
      <DiscardList records={records} />
    </div>
  );
}

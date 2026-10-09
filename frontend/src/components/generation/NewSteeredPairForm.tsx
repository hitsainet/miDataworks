// A steered-pair run (FPRD 007 section 4.2): two settings that differ on ONE feature, the same
// prompt and seed per pair, and the side DPO prefers.
import { useEffect, useState } from 'react';

import { labelingApi } from '@/api/labeling';
import { Button } from '@/components/common/Button';
import { useGenerationStore } from '@/stores/generationStore';
import type { RunCreate, SteeringSetting } from '@/types/generation';

import { AxisDiffLine } from './AxisDiffLine';
import { defaultRespondTemplate } from './format';
import { HeldOutStatus } from './HeldOutStatus';
import { IndependenceCheckRow } from './IndependenceCheckRow';
import { SteeringSettingPicker } from './SteeringSettingPicker';
import { useVersionChoices } from './useVersionChoices';

const FIELD = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';

export function NewSteeredPairForm({ onStarted }: { onStarted: () => void }) {
  const versions = useVersionChoices();
  const templates = useGenerationStore((s) => s.templates);
  const loadTemplates = useGenerationStore((s) => s.loadTemplates);
  const plan = useGenerationStore((s) => s.plan);
  const refusal = useGenerationStore((s) => s.planError);
  const planRun = useGenerationStore((s) => s.planRun);
  const start = useGenerationStore((s) => s.start);
  const independence = useGenerationStore((s) => s.independence);
  const checkIndependence = useGenerationStore((s) => s.checkIndependence);
  const [versionId, setVersionId] = useState('');
  const [sample, setSample] = useState(100);
  const [a, setA] = useState<SteeringSetting>({ kind: 'none' });
  const [b, setB] = useState<SteeringSetting>({ kind: 'profile', profile_name: '' });
  const [chosen, setChosen] = useState<'a' | 'b'>('b');
  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);
  // Steering is miLLM's (the standalone principle, 2026-10-07): ask the generation endpoint
  // what it is at runtime, and say so up front instead of failing on the plan. An unanswered
  // test blocks nothing; the plan still refuses with STEERING_UNSUPPORTED.
  const [serverKind, setServerKind] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    labelingApi
      .testEndpoint('generation')
      .then((r) => { if (live) setServerKind(r.server_kind); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  const notMillm = serverKind !== null && serverKind !== 'millm';
  const respond = defaultRespondTemplate(templates)?.id ?? null;
  const body: RunCreate = {
    mode: 'steered_pairs',
    input_version_id: versionId,
    prompt_column: 'prompt',
    seed_splits: ['train'],
    sample_size: sample,
    respond_template_id: respond,
    setting_a: a,
    setting_b: b,
    chosen_side: chosen,
    target_type: 'dpo',
  };
  return (
    <div className="space-y-3" data-testid="new-steered-run">
      {notMillm && (
        <p role="status" data-testid="steering-unavailable" className="rounded-md border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100">
          Steered pairs need miLLM, which steers a model inline. The generation endpoint is {serverKind === 'tei' ? 'a TEI server' : 'an OpenAI-compatible server'}, which serves no steering. Standard runs work on any endpoint; to build steered pairs, point the generation role at miLLM in Settings.
        </p>
      )}
      <select aria-label="Input version" className={FIELD} value={versionId} onChange={(e) => setVersionId(e.target.value)}>
        <option value="">Choose a DPO version</option>
        {versions.filter((v) => v.target_type === 'dpo').map((v) => (
          <option key={v.id} value={v.id}>{`${v.dataset_name} v${v.number}`}</option>
        ))}
      </select>
      <label className="block text-xs text-slate-500 dark:text-slate-400">Pairs to build
        <input className={FIELD} type="number" min={1} value={sample} onChange={(e) => setSample(Number(e.target.value))} />
      </label>
      <div className="grid sm:grid-cols-2 gap-3">
        <SteeringSettingPicker label="Side A" value={a} onChange={setA} />
        <SteeringSettingPicker label="Side B" value={b} onChange={setB} />
      </div>
      {!notMillm && <AxisDiffLine a={a} b={b} />}
      <p className="text-xs text-slate-500 dark:text-slate-400">Both answers come from the same model and seed. Only the steering differs.</p>
      <fieldset className="flex gap-4 text-sm" aria-label="Chosen side">
        {(['a', 'b'] as const).map((side) => (
          <label key={side} className="flex items-center gap-1">
            <input type="radio" name="chosen" checked={chosen === side} onChange={() => setChosen(side)} /> Prefer side {side.toUpperCase()}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={!versionId || notMillm} onClick={() => void planRun(body)}>Check the plan</Button>
        <Button size="sm" disabled={!versionId || !plan || Boolean(refusal) || notMillm} onClick={async () => { if (await start(body)) onStarted(); }}>
          Build {sample.toLocaleString()} steered pairs
        </Button>
      </div>
      <HeldOutStatus plan={plan} refusal={refusal} />
      {refusal && !refusal.code.startsWith('HELD_OUT') && (
        <div role="alert" data-testid="plan-refusal" className="rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm">
          {refusal.message}
        </div>
      )}
      <IndependenceCheckRow result={independence} onCheck={() => void checkIndependence({ mode: 'steered_pairs', setting_a: a, setting_b: b })} />
    </div>
  );
}

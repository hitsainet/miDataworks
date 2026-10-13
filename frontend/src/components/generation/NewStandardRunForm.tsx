// A standard run (FPRD 007 section 4.2): version, prompt column, seed splits, sample size, N,
// templates, the generator's steering. The plan runs first and shows every refusal with its fix.
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useGenerationStore } from '@/stores/generationStore';
import type { RunCreate, SteeringSetting } from '@/types/generation';

import { defaultRespondTemplate } from './format';
import { HeldOutStatus } from './HeldOutStatus';
import { IndependenceCheckRow } from './IndependenceCheckRow';
import { SteeringSettingPicker } from './SteeringSettingPicker';
import { TryOnSamplePanel } from './TryOnSamplePanel';
import { useVersionChoices } from './useVersionChoices';

const FIELD = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';

export function NewStandardRunForm({ onStarted }: { onStarted: () => void }) {
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
  const [promptColumn, setPromptColumn] = useState('prompt');
  const [splits, setSplits] = useState('train');
  const [sample, setSample] = useState(100);
  const [n, setN] = useState(1);
  // The operator's pick; until there is one, the default respond template (derived, not set by an
  // effect).
  const [respondChoice, setRespond] = useState('');
  const [setting, setSetting] = useState<SteeringSetting>({ kind: 'none' });

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);
  const respond = respondChoice || (defaultRespondTemplate(templates)?.id ?? '');

  const target = (versions.find((v) => v.id === versionId)?.target_type ?? 'sft') as RunCreate['target_type'];
  const body: RunCreate = {
    mode: 'standard',
    input_version_id: versionId,
    prompt_column: promptColumn,
    seed_splits: splits.split(',').map((s) => s.trim()).filter(Boolean),
    sample_size: sample,
    n_responses: n,
    respond_template_id: respond || null,
    generator_setting: setting,
    target_type: target,
  };
  const requests = plan ? plan.expected_requests : sample * n;
  return (
    <div className="space-y-3" data-testid="new-standard-run">
      <label className="block text-xs text-slate-500 dark:text-slate-400" htmlFor="gen-version">Input version</label>
      <select id="gen-version" aria-label="Input version" className={FIELD} value={versionId} onChange={(e) => setVersionId(e.target.value)}>
        <option value="">Choose a version</option>
        {versions.map((v) => (
          <option key={v.id} value={v.id}>{`${v.dataset_name} v${v.number} (${v.target_type})`}</option>
        ))}
      </select>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className="text-xs text-slate-500 dark:text-slate-400">Prompt column
          <input className={FIELD} value={promptColumn} onChange={(e) => setPromptColumn(e.target.value)} />
        </label>
        <label className="text-xs text-slate-500 dark:text-slate-400">Seed splits (comma-separated)
          <input className={FIELD} value={splits} onChange={(e) => setSplits(e.target.value)} />
        </label>
        <label className="text-xs text-slate-500 dark:text-slate-400">Seed rows
          <input className={FIELD} type="number" min={1} value={sample} onChange={(e) => setSample(Number(e.target.value))} />
        </label>
        <label className="text-xs text-slate-500 dark:text-slate-400">Responses per prompt (N)
          <input className={FIELD} type="number" min={1} max={16} value={n} onChange={(e) => setN(Number(e.target.value))} />
        </label>
      </div>
      <label className="block text-xs text-slate-500 dark:text-slate-400" htmlFor="gen-respond">Respond template</label>
      <select id="gen-respond" aria-label="Respond template" className={FIELD} value={respond} onChange={(e) => setRespond(e.target.value)}>
        {templates.filter((t) => t.kind === 'respond').map((t) => (
          <option key={t.id} value={t.id}>{t.ref}</option>
        ))}
      </select>
      <SteeringSettingPicker label="Generator" value={setting} onChange={setSetting} />
      <p className="text-xs text-slate-500 dark:text-slate-400">Every response is kept only when miLLM reports the steering you chose.</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={!versionId} onClick={() => void planRun(body)}>Check the plan</Button>
        <Button
          size="sm"
          disabled={!versionId || !plan || Boolean(refusal)}
          onClick={async () => {
            if (await start(body)) onStarted();
          }}
        >
          Generate {requests.toLocaleString()} responses
        </Button>
      </div>
      <HeldOutStatus plan={plan} refusal={refusal} />
      {refusal && !refusal.code.startsWith('HELD_OUT') && (
        <div role="alert" className="rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm" data-testid="plan-refusal">
          {refusal.message}
        </div>
      )}
      {plan && (
        <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="plan-summary">
          {plan.seed_rows_selected.toLocaleString()} seed rows × {plan.responses_per_prompt} = {plan.expected_requests.toLocaleString()} requests to <span className="text-cyan-600 dark:text-cyan-400">{plan.resident_model ?? 'the endpoint'}</span>
          {plan.server_kind === 'millm' ? ' (miLLM serves one model at a time).' : '.'}
          {plan.warnings.map((w) => <span key={w.code} className="block text-amber-700 dark:text-amber-400">{w.message}</span>)}
        </p>
      )}
      <IndependenceCheckRow result={independence} onCheck={() => void checkIndependence({ mode: 'standard', generator_setting: setting })} />
      <TryOnSamplePanel setting={setting} templateId={respond || null} />
    </div>
  );
}

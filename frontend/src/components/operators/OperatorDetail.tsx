// One operator (FR-003.22): its manifest, a settings form rendered from its schema, its threshold
// statistic, and a preview on a sample. Nothing here writes a version, an event or a job.
import { ArrowLeft } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { useOperatorsStore } from '@/stores/operatorsStore';
import type { OperatorEntry, PreviewRequest, ThresholdStatistic } from '@/types/operators';

import { nextStep, providerLabel, STATE_TEXT } from './OperatorCard';
import { PreviewPanel } from './PreviewPanel';
import { SchemaForm } from './SchemaForm';
import { ThresholdControl } from './ThresholdControl';

/** Thresholds grouped into controls: a band (pair_param) is one control with two cutoffs. */
export function thresholdGroups(thresholds: ThresholdStatistic[]): ThresholdStatistic[][] {
  const seen = new Set<string>();
  const groups: ThresholdStatistic[][] = [];
  for (const t of thresholds) {
    if (seen.has(t.param)) continue;
    const pair = t.pair_param ? thresholds.find((o) => o.param === t.pair_param) : undefined;
    seen.add(t.param);
    if (pair) seen.add(pair.param);
    groups.push(pair ? [t, pair] : [t]);
  }
  return groups;
}

export function OperatorDetail({ entry, onBack }: { entry: OperatorEntry; onBack: () => void }) {
  const fieldErrors = useOperatorsStore((s) => s.fieldErrors);
  const previews = useOperatorsStore((s) => s.previews);
  const statistics = useOperatorsStore((s) => s.statistics);
  const validateParams = useOperatorsStore((s) => s.validateParams);
  const runPreview = useOperatorsStore((s) => s.runPreview);
  const fetchStatistics = useOperatorsStore((s) => s.fetchStatistics);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [versionId, setVersionId] = useState('');
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [statsKey, setStatsKey] = useState<string | null>(null);
  const [valid, setValid] = useState<boolean | null>(null);
  const manifest = entry.manifest;
  const allowed = entry.state === 'allowed';
  const request = (): PreviewRequest => ({ params, input: { version_id: versionId.trim() } });
  const preview = previewKey ? previews[previewKey] : undefined;
  const stats = statsKey ? statistics[statsKey] : undefined;
  const next = nextStep(entry);

  return (
    <div className="space-y-4" data-testid="operator-detail">
      <Button variant="ghost" size="sm" leftIcon={<ArrowLeft size={14} />} onClick={onBack}>
        Back to all operators
      </Button>
      <Card>
        <h2 className="font-mono text-lg text-slate-900 dark:text-slate-100">{entry.ref}</h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{entry.description}</p>
        {next ? <p className="mt-2 text-sm text-amber-600 dark:text-amber-400">{next}</p> : null}
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4">
          {[
            ['Provider', providerLabel(entry.provider)],
            ['Engine version', entry.provider_version ?? '—'],
            ['Kind', entry.kind ?? '—'],
            ['Scope', entry.scope ?? '—'],
            ['State', STATE_TEXT[entry.state]],
            ['Queue', manifest?.resources.queue ?? '—'],
            ['Endpoint role', manifest?.resources.endpoint_role ?? 'none'],
            ['Manifest hash', entry.manifest_hash ? `${entry.manifest_hash.slice(0, 12)}…` : '—'],
          ].map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs text-slate-500 dark:text-slate-400">{k}</dt>
              <dd className="font-mono text-slate-800 dark:text-slate-200">{v}</dd>
            </div>
          ))}
        </dl>
      </Card>
      {manifest ? (
        <Card>
          <h3 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Settings</h3>
          <SchemaForm schema={manifest.params_schema} values={params} onChange={setParams} errors={fieldErrors} readOnly={!allowed} />
          <div className="mt-4 flex flex-wrap items-end gap-3">
            <Button
              variant="secondary"
              disabled={!allowed}
              onClick={async () => setValid(await validateParams(entry.name, entry.version, params))}
            >
              Check settings
            </Button>
            {valid ? <span className="text-sm text-green-600 dark:text-green-400">These settings are valid.</span> : null}
          </div>
        </Card>
      ) : null}
      {manifest && allowed ? (
        <Card>
          <h3 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Try it on a sample</h3>
          <label htmlFor="preview-version" className="block text-sm text-slate-700 dark:text-slate-300">
            Version ID to sample from
          </label>
          <input
            id="preview-version"
            className="mt-1 w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 font-mono text-sm text-slate-900 dark:text-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-400"
            value={versionId}
            onChange={(e) => setVersionId(e.target.value)}
          />
          <div className="mt-3 flex flex-wrap gap-3">
            <Button disabled={!versionId.trim()} onClick={async () => setPreviewKey(await runPreview(entry.name, entry.version, request()))}>
              Preview on a sample
            </Button>
            {manifest.thresholds.length ? (
              <Button
                variant="secondary"
                disabled={!versionId.trim()}
                onClick={async () => setStatsKey(await fetchStatistics(entry.name, entry.version, request()))}
              >
                Show the statistic
              </Button>
            ) : null}
          </div>
          {preview?.loading ? <p className="mt-3 text-sm text-slate-500">Running the preview…</p> : null}
          {preview?.error ? <p role="alert" className="mt-3 text-sm text-red-500">{preview.error}</p> : null}
          {preview?.data ? <div className="mt-4"><PreviewPanel result={preview.data} /></div> : null}
          {stats?.loading ? <p className="mt-3 text-sm text-slate-500">Computing the statistic…</p> : null}
          {stats?.error ? <p role="alert" className="mt-3 text-sm text-red-500">{stats.error}</p> : null}
          {stats?.data
            ? thresholdGroups(stats.data.thresholds).map((group) => (
                <div className="mt-4" key={group.map((g) => g.param).join('+')}>
                  <ThresholdControl
                    thresholds={group}
                    sampleSize={stats.data!.sample_size}
                    onCommit={(cut) => setParams({ ...params, ...cut })}
                  />
                </div>
              ))
            : null}
        </Card>
      ) : null}
    </div>
  );
}


// The open run's "at a glance" block (operator request 2026-10-21): purpose, target dataset, when,
// who, how and outcome, each in words. Names come first; ids sit in tooltips and small print.
import type { ReactNode } from 'react';

import { useVersionsStore } from '@/stores/versionsStore';
import type { GenerationRun, NamedVersion } from '@/types/generation';
import { formatDuration, formatEt } from '@/utils/format';
import { navigate } from '@/utils/navigate';

import { settingText } from './format';
import {
  MODE_EXPLAIN,
  NO_BEHAVIOUR,
  NO_BEHAVIOUR_HOW,
  REVISION_TOOLTIP,
  TARGET_WORDS,
  behaviourSourceText,
  discardedCount,
  generatedCount,
  seedText,
  topDiscardReasons,
  versionName,
  whenLine,
  whoText,
} from './runSummary';

function Row({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 py-1 border-t border-slate-100 dark:border-slate-700/60" data-testid={testId}>
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-slate-800 dark:text-slate-200 min-w-0">{children}</dd>
    </div>
  );
}

function VersionLink({ version }: { version: NamedVersion }) {
  const select = useVersionsStore((s) => s.selectVersion);
  return (
    <button
      type="button"
      className="text-indigo-600 dark:text-indigo-400 underline underline-offset-2"
      title={`Version id ${version.id}`}
      onClick={() => {
        select(version.id);
        navigate('version');
      }}
    >
      {versionName(version)}
    </button>
  );
}

function seconds(value: number | null | undefined): string {
  return value === null || value === undefined ? 'not recorded' : formatDuration(value);
}

export function RunAtAGlance({ run, now }: { run: GenerationRun; now?: number }) {
  const t = run.timing;
  const behaviours = run.behaviours ?? (run.behaviour ? [run.behaviour] : []);
  const template = run.respond_template ?? run.expand_template ?? null;
  const sampling = template?.sampling ?? null;
  const reasons = topDiscardReasons(run);
  const chain = run.minimal_pair_chain ?? null;
  const candidates = run.candidate_versions ?? [];
  const judge = run.judge_identity;
  // Steered pairs record one snapshot per side; the other modes record the generator's.
  const steering = run.snapshots.filter((s) => s.side !== 'generator' || run.mode !== 'steered_pairs');
  return (
    <dl className="text-xs" data-testid="run-at-a-glance">
      <Row label="Behaviour" testId="glance-behaviour">
        {behaviours.length === 0 ? (
          <span>
            <span className="font-medium">{NO_BEHAVIOUR}.</span> <span className="text-slate-500 dark:text-slate-400">{NO_BEHAVIOUR_HOW}</span>
          </span>
        ) : (
          <ul className="stack-y-1">
            {behaviours.map((b) => (
              <li key={`${b.source}-${b.source_id}`}>
                {b.key ? <span className="font-mono font-medium">{b.key}</span> : <span className="text-slate-500 dark:text-slate-400">no behaviour key set</span>}
                {b.meaning ? <> — positive means: {b.meaning}</> : null}
                <span className="text-slate-500 dark:text-slate-400"> ({behaviourSourceText(b)})</span>
              </li>
            ))}
          </ul>
        )}
        {run.behaviour_note ? (
          <div className="text-amber-700 dark:text-amber-300" data-testid="glance-behaviour-note">{run.behaviour_note}</div>
        ) : null}
      </Row>
      <Row label="Dataset" testId="glance-dataset">
        {run.input_version ? <VersionLink version={run.input_version} /> : <span className="font-mono">version {run.input_version_id.slice(0, 8)}</span>}
        <span className="text-slate-500 dark:text-slate-400"> → {TARGET_WORDS[run.target_type] ?? run.target_type}</span>
        <div className="text-[11px] text-slate-400 dark:text-slate-500 font-mono truncate">version id {run.input_version_id}</div>
      </Row>
      <Row label="When" testId="glance-when">
        <div>{whenLine(run, now)}</div>
        <div className="text-slate-500 dark:text-slate-400">
          Queued {formatEt(t?.queued_at ?? run.created_at) ?? 'time not recorded'}
          {t?.queued_seconds != null && t.queued_seconds >= 1 ? ` · waited ${formatDuration(t.queued_seconds)} for a worker` : ''}
          {t?.active_seconds != null ? ` · working time ${seconds(t.active_seconds)}` : ''}
          {t && t.attempts > 1 ? ` · ${t.attempts} attempts (resumed ${t.attempts - 1} time${t.attempts === 2 ? '' : 's'})` : ''}
        </div>
      </Row>
      <Row label="Who" testId="glance-who">
        {run.started_by_origin === 'agent' ? (
          <>
            Started by {whoText(run)}
            {run.approval_id ? (
              <> · approved by the operator (<button type="button" className="text-indigo-600 dark:text-indigo-400 underline underline-offset-2" onClick={() => navigate('approvals')} title={`Approval id ${run.approval_id}`}>see Approvals</button>)</>
            ) : (
              <span className="text-slate-500 dark:text-slate-400"> · a generation run needs no approval</span>
            )}
          </>
        ) : (
          <>Started by {run.started_by} (operator)</>
        )}
      </Row>
      <Row label="How" testId="glance-how">
        <div>{MODE_EXPLAIN[run.mode] ?? run.mode}</div>
        {chain && (
          <div>
            Part of a minimal-pair chain judged with {chain.rubric_name ?? 'a rubric no longer present'}
            {chain.flip_from && chain.flip_to ? `, flipping the verdict from "${chain.flip_from}" to "${chain.flip_to}"` : ''} (chain {chain.state}).
          </div>
        )}
        <div>
          Template: {template ? <span title={template.description ?? undefined}>{template.name} v{template.version} ({template.kind})</span> : 'not recorded'}
          {sampling ? <span className="text-slate-500 dark:text-slate-400"> · temperature {sampling.temperature ?? 'not set'}, top-p {sampling.top_p ?? 'not set'}, up to {sampling.max_tokens ?? '?'} tokens</span> : null}
        </div>
        {template?.description && <div className="text-slate-500 dark:text-slate-400">{template.description}</div>}
        <div>
          Generator: <span className={run.server_kind === 'millm' ? 'text-cyan-600 dark:text-cyan-400' : ''}>{String(run.generation_endpoint.model_id ?? 'not recorded')}</span>
          <span className="text-slate-500 dark:text-slate-400"> on the {String(run.generation_endpoint.role ?? 'generation')} endpoint ({run.server_kind === 'millm' ? 'miLLM' : run.server_kind.replace(/_/g, ' ')}, {run.engine_path} path)</span>
          {run.revision_reported === false && <span className="text-slate-400 dark:text-slate-500" title={REVISION_TOOLTIP}> · revision not reported</span>}
        </div>
        <div>
          Judge: {judge ? <>{judge.model_id}{judge.revision === 'not reported' ? <span className="text-slate-400 dark:text-slate-500" title={REVISION_TOOLTIP}> · revision not reported</span> : <span className="font-mono text-slate-500 dark:text-slate-400"> @ {judge.revision.slice(0, 12)}</span>}</> : 'none configured when the run started'}
        </div>
        <div>Steering: {steering.length ? steering.map((s) => `${s.side === 'generator' ? '' : `side ${s.side}: `}${settingText(s.kind, s.profile_name, s.features.length)}`).join(' · ') : 'none recorded'}</div>
        <div>Seeds: {seedText(run)} · column {run.prompt_column} · seed {run.seed}</div>
      </Row>
      <Row label="Outcome" testId="glance-outcome">
        <div>
          {generatedCount(run).toLocaleString()} generated · {discardedCount(run).toLocaleString()} discarded · {(run.counts.pairs ?? 0).toLocaleString()} pairs
        </div>
        {reasons && <div className="text-slate-500 dark:text-slate-400">Top discard reasons: {reasons}</div>}
        {chain?.pair_version && <div>Pairs: <VersionLink version={chain.pair_version} /></div>}
        <div>
          Candidate version:{' '}
          {candidates.length ? candidates.map((c, i) => <span key={c.id}>{i ? ', ' : ''}<VersionLink version={c} /></span>) : run.state === 'completed' ? 'none yet (use Build candidate version)' : 'can be built once the run completes'}
        </div>
      </Row>
    </dl>
  );
}

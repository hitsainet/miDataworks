// Who labeled this, with what (FR-005.24 – FR-005.27; US-5). Missing facts read "not reported".
import type { LabelRun } from '@/types/labeling';

function pinnedText(run: LabelRun): string {
  const pinned = run.pinned ?? run.endpoint_snapshot.pinned ?? null;
  if (pinned === null) return 'not known yet';
  if (pinned) return 'yes, under the miLLM lease';
  return run.endpoint_snapshot.unpinned_reason ? `no (unpinned): ${run.endpoint_snapshot.unpinned_reason}` : 'no (unpinned)';
}

const shown = (value: unknown) => (value === null || value === undefined || value === '' ? 'not reported' : String(value));

export function ProvenanceBlock({ run }: { run: LabelRun }) {
  const s = run.endpoint_snapshot;
  const rows: Array<[string, string]> = [
    ['Started by', `${run.started_by} (${run.started_by_origin})`],
    ['Endpoint', `${shown(s.role)} · ${shown(s.protocol)} · ${shown(s.base_url)}`],
    ['Model', `${shown(s.model_id)} @ ${shown(s.model_revision)}`],
    ['Server', shown(s.server_kind)],
    ...(run.kind === 'probe_verdict'
      ? ([
          ['Probe', `${shown(s.probe?.probe_id)} · window ${shown(s.probe?.window)} · layer ${shown(s.probe?.layer)} · fitted on ${shown(s.probe?.hf_id)}`],
          ['miStudio probe', `${shown(s.probe?.mistudio_probe_id)} · run ${shown(s.probe?.mistudio_run_id)}`],
        ] as Array<[string, string]>)
      : []),
    ['Pinned', pinnedText(run)],
    ['Revision reported', run.revision_reported ? 'yes' : 'no'],
    ['Template or rubric', shown(run.template_ref)],
    ['Question', shown(run.question)],
    ['Thresholds', run.threshold_positive === null ? 'none' : `positive ≥ ${run.threshold_positive}, negative ≤ ${run.threshold_negative}`],
    ['Sampling', JSON.stringify(run.sampling)],
    ['Structured output', run.structured_output],
    ['Rows sent', run.packing],
    ['System fingerprint', shown(run.system_fingerprint)],
    ['Labeler identity', run.labeler_identity_hash.slice(0, 16)],
    ['Fingerprint', run.labeler_fingerprint.slice(0, 16)],
  ];
  return (
    <dl data-testid="provenance" className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="font-mono break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

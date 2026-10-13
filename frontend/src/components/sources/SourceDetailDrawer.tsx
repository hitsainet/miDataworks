// One source in full (FR-001.30): identity, files and hashes, licence and its annotation history,
// detection with an override, delete, and "Build a version from this source" (FR-001.33). An
// agent's annotation waits for approval (S3-01); the operator's lands at once.
import { X } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { useDraftsStore } from '@/stores/draftsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { SourceDetail } from '@/types/sources';
import { formatBytes, formatCount } from '@/utils/format';
import { navigate } from '@/utils/navigate';

import { CommitChip } from './CommitChip';
import { DetectionPanel } from './DetectionPanel';
import { draftPatchFor } from './handoff';
import { LicenceBadge } from './LicenceBadge';
import { StatePill } from './SourcesList';

const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';
const sectionCls = 'border-t border-slate-200 dark:border-slate-700 pt-3 mt-3';

function AnnotationForm({ source }: { source: SourceDetail }) {
  const meta = useSourcesStore((s) => s.meta);
  const annotateSource = useSourcesStore((s) => s.annotateSource);
  const [kind, setKind] = useState('terms');
  const [redistribution, setRedistribution] = useState('');
  const [reason, setReason] = useState('');
  const kinds = (meta?.annotation_kinds ?? ['terms', 'licence']).filter((k) => k !== 'detection_override');
  const save = async () => {
    if (await annotateSource(source.id, { kind, redistribution: redistribution || null, value: {}, reason: reason.trim() })) {
      setReason('');
      setRedistribution('');
    }
  };
  return (
    <div className="grid gap-2 sm:grid-cols-2 text-xs" data-testid="annotation-form">
      <label className="flex flex-col gap-1">What you are recording
        <select value={kind} onChange={(e) => setKind(e.target.value)} className={field}>{kinds.map((k) => <option key={k} value={k}>{k}</option>)}</select>
      </label>
      <label className="flex flex-col gap-1">Redistribution
        <select value={redistribution} onChange={(e) => setRedistribution(e.target.value)} className={field} aria-label="Redistribution">
          <option value="">Choose…</option>
          {(meta?.redistribution ?? []).map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 sm:col-span-2">Reason (where you read it)
        <input value={reason} onChange={(e) => setReason(e.target.value)} className={field} />
      </label>
      <div className="sm:col-span-2 flex justify-end">
        <Button size="sm" onClick={() => void save()} disabled={!reason.trim() || !redistribution}>Record the {kind}</Button>
      </div>
    </div>
  );
}

function OverrideForm({ source }: { source: SourceDetail }) {
  const overrideDetection = useSourcesStore((s) => s.overrideDetection);
  const columns = source.files[0]?.columns.map((c) => c.name) ?? [];
  const [text, setText] = useState(source.detection?.text_columns[0] ?? '');
  const [label, setLabel] = useState(source.detection?.label_columns[0] ?? '');
  const [reason, setReason] = useState('');
  const save = async () => {
    const value: Record<string, unknown> = { text_columns: text ? [text] : [] };
    value.label_columns = label ? [label] : [];
    if (await overrideDetection(source.id, value, reason.trim())) setReason('');
  };
  return (
    <div className="grid gap-2 sm:grid-cols-2 text-xs" data-testid="override-form">
      <label className="flex flex-col gap-1">Text column
        <select value={text} onChange={(e) => setText(e.target.value)} className={field}><option value="">none</option>{columns.map((c) => <option key={c}>{c}</option>)}</select>
      </label>
      <label className="flex flex-col gap-1">Label column
        <select value={label} onChange={(e) => setLabel(e.target.value)} className={field}><option value="">none</option>{columns.map((c) => <option key={c}>{c}</option>)}</select>
      </label>
      <label className="flex flex-col gap-1 sm:col-span-2">Reason
        <input value={reason} onChange={(e) => setReason(e.target.value)} className={field} />
      </label>
      <div className="sm:col-span-2 flex justify-end"><Button size="sm" variant="secondary" onClick={() => void save()} disabled={!reason.trim()}>Override the detection</Button></div>
    </div>
  );
}

export function SourceDetailDrawer() {
  const source = useSourcesStore((s) => s.selected);
  const error = useSourcesStore((s) => s.error);
  const closeSource = useSourcesStore((s) => s.closeSource);
  const deleteSource = useSourcesStore((s) => s.deleteSource);
  const loadDraft = useDraftsStore((s) => s.loadDraft);
  const updateDraft = useDraftsStore((s) => s.updateDraft);
  const [confirming, setConfirming] = useState(false);
  const [deleteReason, setDeleteReason] = useState('');

  // Another source closes an open delete confirmation (adjusted during render, not in an effect).
  const [confirmingFor, setConfirmingFor] = useState(source?.id);
  if (confirmingFor !== source?.id) {
    setConfirmingFor(source?.id);
    setConfirming(false);
  }
  if (!source) return null;

  const build = async () => {
    const draft = await loadDraft();
    if (!draft) return;
    updateDraft(draftPatchFor(draft, source));
    closeSource();
    navigate('new-dataset');
  };

  return (
    <aside role="dialog" aria-label={`Source ${source.display_name}`} data-testid="source-drawer" className="fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto border-l border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-lg text-slate-900 dark:text-slate-100 break-words">{source.display_name}</h2>
          <div className="flex flex-wrap items-center gap-2 mt-1 text-xs text-slate-500 dark:text-slate-400">
            <StatePill state={source.state} /> {source.kind === 'hf' ? 'Hugging Face' : 'Upload'} · by {source.created_by}
          </div>
        </div>
        <button type="button" aria-label="Close the source" onClick={closeSource} className="p-1 rounded text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><X className="w-5 h-5" /></button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">{error}</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm mt-4">
        {source.repo_id && <><dt className="text-slate-500">Repository</dt><dd className="font-mono break-all">{source.repo_id}</dd></>}
        {source.config && <><dt className="text-slate-500">Config</dt><dd className="font-mono">{source.config}</dd></>}
        {source.resolved_commit && <><dt className="text-slate-500">Commit</dt><dd><CommitChip value={source.resolved_commit} />{source.requested_ref && <span className="text-xs text-slate-500"> from {source.requested_ref}</span>}</dd></>}
        {source.content_hash && <><dt className="text-slate-500">Content hash</dt><dd><CommitChip value={source.content_hash} /></dd></>}
        {source.token_tier && <><dt className="text-slate-500">Token used</dt><dd>{source.token_tier.replace('_', '-')}</dd></>}
        <dt className="text-slate-500">Size</dt><dd>{formatCount(source.rows, 'row')} in {formatCount(source.files.length, 'split')}</dd>
        {source.error?.message && <><dt className="text-slate-500">Error</dt><dd className="text-red-700 dark:text-red-400">{source.error.message}</dd></>}
        {source.deleted_at && <><dt className="text-slate-500">Deleted</dt><dd>by {source.deleted_by}; record and hashes kept</dd></>}
      </dl>
      <section className={sectionCls}>
        <h3 className="font-semibold mb-2">Files</h3>
        <table className="w-full text-xs text-left">
          <thead><tr className="text-slate-500"><th>Split</th><th className="text-right">Rows</th><th className="text-right">Size</th><th>SHA-256</th></tr></thead>
          <tbody>
            {source.files.map((f) => (
              <tr key={f.split} className="border-t border-slate-200 dark:border-slate-700">
                <td className="font-mono py-1">{f.split}{f.original_name && <span className="font-sans text-slate-500"> ({f.original_name})</span>}</td>
                <td className="text-right">{f.rows.toLocaleString('en-US')}</td>
                <td className="text-right">{formatBytes(f.bytes)}</td>
                <td><CommitChip value={f.sha256} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className={sectionCls}>
        <h3 className="font-semibold mb-2">Licence and terms</h3>
        <LicenceBadge display={source.licence.display} terms={source.licence.terms_status} verbose />
        <p className="text-xs mt-1 text-slate-500 dark:text-slate-400">As imported: {source.licence.raw ?? 'nothing'} ({source.licence.origin ?? 'no origin'}). The Hub value is never rewritten.</p>
        {source.licence.history.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs" data-testid="annotation-history">
            {source.licence.history.map((a) => (
              <li key={a.id}>{a.kind}: {a.redistribution ?? 'override'} — {a.reason} <span className="text-slate-500">({a.created_by}{a.approved_by ? `, approved by ${a.approved_by}` : ''})</span></li>
            ))}
          </ul>
        )}
        {source.state !== 'deleted' && <div className="mt-3"><AnnotationForm source={source} /></div>}
      </section>
      <section className={sectionCls}>
        <h3 className="font-semibold mb-2">Detected</h3>
        <DetectionPanel detection={source.detection} />
        {source.state === 'ready' && <div className="mt-3"><OverrideForm source={source} /></div>}
      </section>
      {source.state !== 'deleted' && (
        <section className={`${sectionCls} flex flex-wrap justify-between gap-2`}>
          {!confirming ? (
            <Button variant="danger" size="sm" onClick={() => setConfirming(true)} disabled={source.state === 'importing'}>Delete the source</Button>
          ) : (
            <div className="w-full text-sm" data-testid="delete-confirm">
              <p className="mb-2">Deleting removes this source's files. Its record, hashes and licence notes stay. A source a version was built from is kept.</p>
              <input aria-label="Reason for deleting" placeholder="Reason" value={deleteReason} onChange={(e) => setDeleteReason(e.target.value)} className={`${field} mb-2`} />
              <div className="flex gap-2">
                <Button variant="danger" size="sm" disabled={!deleteReason.trim()} onClick={() => void deleteSource(source.id, deleteReason.trim())}>Delete its files</Button>
                <Button variant="secondary" size="sm" onClick={() => setConfirming(false)}>Keep the source</Button>
              </div>
            </div>
          )}
          {source.state === 'ready' && !confirming && <Button size="sm" onClick={() => void build()}>Build a version from this source</Button>}
        </section>
      )}
    </aside>
  );
}

// Option (b) of the reproduction gate (009 FR-009.77, operator decision 2026-10-07): when a probe has
// no recorded target, the refusal still shows the probe, its one-input preflight against the bar and
// the reproduction state, names BOTH ways through, and offers "Link a miStudio evaluation". Every
// check a link ran is shown, and a link whose content was not compared says "counts only"; a hash
// match is never claimed unless both hashes were computed.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { useLabelingStore } from '@/stores/labelingStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { LinkCandidate, LinkChecks, ReproductionRefusal } from '@/types/labeling';

import { ProbePlanLine, ScoringFormLine, conflictText, keysText } from './ProbeLines';

const n = (x: number) => x.toLocaleString();
const f = (x: number | null | undefined) => (x === null || x === undefined ? 'not reported' : x.toFixed(4));

export function candidateText(c: LinkCandidate): string {
  return `${c.view_name ?? c.probe_dataset_id} · ${c.distribution ?? 'distribution not stated'} · AUROC ${f(c.auroc)} [${f(c.ci[0])}, ${f(c.ci[1])}] on ${n(c.n_positive + c.n_negative)} rows`;
}

export function checkLevelText(level: string): string {
  if (level === 'content') return 'rows checked by content hash';
  if (level === 'counts_only') return 'counts only: row content was not compared';
  return 'refused: these are not the rows miStudio evaluated';
}

export function ChecksList({ checks }: { checks: LinkChecks }) {
  const { row_count: rc, class_balance: bal, content } = checks;
  const mark = (ok: boolean) => (ok ? 'agrees' : 'differs');
  let contentLine: string;
  if (!content.ran) contentLine = `Content: not compared. ${content.reason ?? ''}`;
  else if (content.passed) {
    contentLine = `Content: input texts hash the same${content.ordered_match ? ', in the same order' : ' (same rows, another order)'} (sha256 ${content.ours_sha256?.unordered.slice(0, 12)}…).`;
  } else contentLine = 'Content: the input texts hash differently from the rows miStudio served.';
  return (
    <ul className="text-sm list-disc pl-5 space-y-0.5" data-testid="link-checks" data-level={checks.level}>
      <li>Row count: {n(rc.ours)} rows map to a class here, miStudio evaluated {n(rc.mistudio)} ({mark(rc.passed)}).</li>
      <li>
        Class balance: {n(bal.ours.positive)} positive / {n(bal.ours.negative)} negative here, {n(bal.mistudio.positive)} / {n(bal.mistudio.negative)} in miStudio ({mark(bal.passed)}).
      </li>
      <li data-testid="link-content">{contentLine}</li>
      {checks.row_keys && <li data-testid="link-keys">Counted: {keysText(checks.row_keys)}.</li>}
      {checks.row_keys && conflictText(checks.row_keys) && (
        <li className="text-amber-700 dark:text-amber-400" data-testid="link-conflicts">{conflictText(checks.row_keys)}</li>
      )}
    </ul>
  );
}

export function ReproductionRefusalBlock({ refusal, onLinked }: { refusal: ReproductionRefusal; onLinked: () => void }) {
  const versionList = useVersionsStore((s) => s.versionList);
  const createLink = useLabelingStore((s) => s.createLink);
  const link = useLabelingStore((s) => s.link);
  const linkError = useLabelingStore((s) => s.linkError);
  const linkApproval = useLabelingStore((s) => s.linkApproval);
  const linkBusy = useLabelingStore((s) => s.linkBusy);
  const [open, setOpen] = useState(false);
  const [viewId, setViewId] = useState('');
  const [versionId, setVersionId] = useState('');
  const [split, setSplit] = useState('');
  const [inputColumn, setInputColumn] = useState('');
  const [labelColumn, setLabelColumn] = useState('');
  const candidates = refusal.link_candidates.items;
  const chosen = candidates.find((c) => c.probe_dataset_id === viewId);

  const choose = (id: string) => {
    setViewId(id);
    const c = candidates.find((x) => x.probe_dataset_id === id);
    setSplit(c?.split ?? '');
    setInputColumn(c?.input_column ?? '');
    setLabelColumn(c?.label_column ?? '');
  };
  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  const submit = async () => {
    if (!refusal.mistudio_probe_id) return;
    const made = await createLink({
      mistudio_probe_id: refusal.mistudio_probe_id,
      probe_dataset_id: viewId,
      version_id: versionId,
      split: split.trim(),
      input_column: inputColumn.trim() || null,
      label_column: labelColumn.trim() || null,
    });
    if (made) onLinked();
  };

  return (
    <div className="mb-3 rounded border border-amber-300 dark:border-amber-700 px-3 py-2 space-y-2" data-testid="reproduction-refusal">
      <ProbePlanLine probe={refusal.probe} />
      <p className="text-sm text-red-600 dark:text-red-400" data-testid="reproduction-unavailable" data-state={refusal.reproduction.state}>
        Reproduction unavailable: no recorded miStudio evaluation of {refusal.mistudio_probe_id ?? 'this probe'} can be the target, so no probe verdict is written.
      </p>
      <ol className="text-sm list-[lower-alpha] pl-5 space-y-0.5" data-testid="reproduction-ways">
        {refusal.reproduction.ways.map((w) => <li key={w.way}>{w.what}</li>)}
      </ol>
      {!open ? (
        <Button variant="secondary" onClick={() => setOpen(true)} disabled={!refusal.mistudio_probe_id}>Link a miStudio evaluation</Button>
      ) : (
        <div className="space-y-2" data-testid="link-form">
          {candidates.length === 0 ? (
            <p className="text-sm text-slate-600 dark:text-slate-300" data-testid="link-candidates-reason">{refusal.link_candidates.reason ?? 'miStudio lists no evaluation of this probe.'}</p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs sm:col-span-2"><span className="block text-slate-500 dark:text-slate-400 mb-1">miStudio evaluation</span>
                <select aria-label="miStudio evaluation" value={viewId} onChange={(e) => choose(e.target.value)} className={field}>
                  <option value="">Choose the evaluation to reproduce</option>
                  {candidates.map((c) => <option key={c.probe_dataset_id} value={c.probe_dataset_id}>{candidateText(c)}</option>)}
                </select>
                {chosen && (
                  <span className="block mt-1 text-slate-500 dark:text-slate-400">
                    miStudio read {chosen.mistudio_dataset_id ?? 'its dataset'} split {chosen.split ?? 'not stated'}, column {chosen.input_column}, labels {chosen.label_column}. Link the version split holding exactly those rows.
                  </span>
                )}
              </label>
              <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Version holding those rows</span>
                <select aria-label="Version holding those rows" value={versionId} onChange={(e) => setVersionId(e.target.value)} className={field}>
                  <option value="">Choose a version</option>
                  {versionList.map((v) => <option key={v.id} value={v.id}>{v.dataset_name} v{v.number} ({n(v.total_rows)} rows)</option>)}
                </select>
              </label>
              <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Split</span>
                <input aria-label="Split" value={split} onChange={(e) => setSplit(e.target.value)} className={`${field} font-mono`} />
              </label>
              <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Input column</span>
                <input aria-label="Input column" value={inputColumn} onChange={(e) => setInputColumn(e.target.value)} className={`${field} font-mono`} />
              </label>
              <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Label column</span>
                <input aria-label="Label column" value={labelColumn} onChange={(e) => setLabelColumn(e.target.value)} className={`${field} font-mono`} />
              </label>
              <div className="sm:col-span-2">
                <Button disabled={!viewId || !versionId || !split.trim() || linkBusy} loading={linkBusy} onClick={() => void submit()}>Check and link</Button>
              </div>
            </div>
          )}
          {linkError && (
            <div role="alert" className="text-sm text-red-600 dark:text-red-400 space-y-1" data-testid="link-error">
              <p>{linkError.message}</p>
              {linkError.checks && <ChecksList checks={linkError.checks} />}
            </div>
          )}
          {linkApproval && <p role="status" className="text-sm">Waiting for the operator to approve this link ({linkApproval.approval_id}).</p>}
          {link && (
            <div role="status" className="text-sm space-y-1" data-testid="link-made" data-level={link.check_level}>
              <p>Linked {link.id}: {checkLevelText(link.check_level)}.</p>
              <ChecksList checks={link.checks} />
              <ScoringFormLine form={link.scoring_form} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

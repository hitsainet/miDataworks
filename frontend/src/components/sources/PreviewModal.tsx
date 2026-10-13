// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/datasets/DatasetPreviewModal.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: the modal with loading and error states, the splits table with
// a radio to choose one, and the action filling the form. Changed (FR-001.15–001.18): the data comes
// from the backend preview (sourcesStore.previewHf), never the browser calling Hugging Face; a
// commit line with the head note; a config list that is never auto-chosen; a sample-row table with
// truncation markers; the detection panel; "unavailable" notices for parts the Viewer could not
// serve.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { TruncatedCell } from '@/types/sources';
import { formatBytes, formatCount } from '@/utils/format';

import { CommitChip } from './CommitChip';
import { DetectionPanel } from './DetectionPanel';
import { LicenceBadge } from './LicenceBadge';

function cell(value: unknown): string {
  if (value && typeof value === 'object' && (value as TruncatedCell).truncated) {
    const t = value as TruncatedCell;
    return `${t.text}… (${t.length.toLocaleString('en-US')} characters)`;
  }
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onPickConfig: (config: string) => void;
  onPickSplit: (split: string) => void;
  onImport: () => void;
}

export function PreviewModal({ isOpen, onClose, onPickConfig, onPickSplit, onImport }: Props) {
  const preview = useSourcesStore((s) => s.preview);
  const status = useSourcesStore((s) => s.previewStatus);
  const error = useSourcesStore((s) => s.previewError);
  const closePreview = useSourcesStore((s) => s.closePreview);
  const [split, setSplit] = useState<string | null>(null);
  const close = () => {
    closePreview();
    setSplit(null);
    onClose();
  };
  const needsConfig = preview && !preview.config && preview.configs.length > 1;
  const rowsTotal = preview?.splits.reduce((n, s) => n + (s.rows ?? 0), 0) ?? 0;

  return (
    <Modal
      id="source-preview"
      isOpen={isOpen}
      onClose={close}
      size="3xl"
      title={preview ? `Preview: ${preview.repo_id}` : 'Preview'}
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>Close the preview</Button>
          {preview && split && <Button variant="secondary" onClick={() => { onPickSplit(split); close(); }}>Use this split</Button>}
          {preview && !needsConfig && <Button onClick={onImport}>Import</Button>}
        </div>
      }
    >
      <div data-testid="preview-modal" className="space-y-4 text-sm">
        {status === 'loading' && <p role="status" className="text-slate-500 dark:text-slate-400">Asking Hugging Face for the commit, splits and sample rows…</p>}
        {status === 'error' && <p role="alert" className="text-red-700 dark:text-red-400">{error}</p>}
        {preview && (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span>Commit <CommitChip value={preview.resolved_commit} /></span>
              {preview.requested_ref && <span className="text-slate-500 dark:text-slate-400">from <span className="font-mono">{preview.requested_ref}</span></span>}
              <LicenceBadge display={preview.licence.display} />
              {preview.gated && preview.gated !== 'false' && <span className="text-xs text-amber-700 dark:text-amber-400">Gated ({preview.gated}): accept its terms on the Hub with the token's account.</span>}
            </div>
            {preview.viewer_commit_note && <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="head-note">{preview.viewer_commit_note}</p>}
            {preview.unavailable.map((u) => (
              <p key={u.part} className="text-xs text-slate-500 dark:text-slate-400" data-testid="unavailable">
                {u.part}: {u.reason}
              </p>
            ))}
            {preview.configs.length > 1 && (
              <section>
                <h3 className="font-semibold mb-1">Configs</h3>
                {needsConfig && <p className="text-xs mb-2 text-amber-700 dark:text-amber-400">This dataset has {preview.configs.length} configs. Choose one to see its splits; none is chosen for you.</p>}
                <div className="flex flex-wrap gap-2">
                  {preview.configs.map((c) => (
                    <Button key={c} size="sm" variant={c === preview.config ? 'primary' : 'secondary'} onClick={() => onPickConfig(c)}>{`Use config ${c}`}</Button>
                  ))}
                </div>
              </section>
            )}
            {preview.splits.length > 0 && (
              <section>
                <h3 className="font-semibold mb-1">Splits</h3>
                <p className="text-xs mb-2 text-slate-500 dark:text-slate-400">
                  {formatCount(rowsTotal, 'row')} in {formatCount(preview.splits.length, 'split')}
                  {preview.size?.num_bytes_parquet_files ? `, ${formatBytes(preview.size.num_bytes_parquet_files)} Parquet (Hub-reported)` : ''}
                </p>
                <table className="w-full text-left">
                  <thead><tr className="text-xs text-slate-500 dark:text-slate-400"><th className="py-1">Choose</th><th>Split</th><th className="text-right">Rows</th><th className="text-right">Parquet size</th></tr></thead>
                  <tbody>
                    {preview.splits.map((s) => (
                      <tr key={s.name} className="border-t border-slate-200 dark:border-slate-700">
                        <td className="py-1"><input type="radio" name="preview-split" aria-label={`Choose split ${s.name}`} checked={split === s.name} onChange={() => setSplit(s.name)} className="focus:ring-2 focus:ring-indigo-500" /></td>
                        <td className="font-mono">{s.name}</td>
                        <td className="text-right">{s.rows === null ? '—' : s.rows.toLocaleString('en-US')}</td>
                        <td className="text-right font-mono">{s.bytes === null ? '—' : formatBytes(s.bytes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {preview.sample_rows.length > 0 && (
              <section>
                <h3 className="font-semibold mb-1">Sample rows ({formatCount(preview.sample_rows.length, 'row')}, first rows of {preview.split ?? preview.splits[0]?.name ?? 'the first split'})</h3>
                <div className="max-h-64 overflow-auto rounded border border-slate-200 dark:border-slate-700">
                  <table className="w-full text-left text-xs" data-testid="sample-table">
                    <thead className="sticky top-0 bg-slate-100 dark:bg-slate-800">
                      <tr>{preview.columns.map((c) => <th key={c.name} className="px-2 py-1 font-mono whitespace-nowrap">{c.name} <span className="font-sans text-slate-500">{c.type}</span></th>)}</tr>
                    </thead>
                    <tbody>
                      {preview.sample_rows.map((row, i) => (
                        <tr key={i} className="border-t border-slate-200 dark:border-slate-700 align-top">
                          {preview.columns.map((c) => <td key={c.name} className="px-2 py-1 max-w-xs break-words">{cell(row[c.name])}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
            <section>
              <h3 className="font-semibold mb-1">Detected</h3>
              <DetectionPanel detection={preview.detection} />
            </section>
          </>
        )}
      </div>
    </Modal>
  );
}

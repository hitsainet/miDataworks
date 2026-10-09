// Upload Parquet, JSONL or CSV files as one source, each file a split (FR-001.21–001.25, T-03).
// Drag-and-drop or a picker; each file shows its split name, size and the format its name suggests
// (the backend sniffs the content and refuses a mismatch). CSV options sit behind "CSV options".
import { Upload, X } from 'lucide-react';
import { useState } from 'react';
import type { DragEvent } from 'react';

import { Button } from '@/components/common/Button';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { ImportOutcome } from '@/stores/sourcesStore';
import type { CsvOptions, UploadManifest } from '@/types/sources';
import { formatBytes, formatCount } from '@/utils/format';

const SUGGESTED = ['train', 'test', 'validation'];

export function formatFromName(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith('.parquet')) return 'Parquet';
  if (/\.(jsonl|ndjson|json)$/.test(lower)) return 'JSONL';
  if (/\.(csv|tsv|txt)$/.test(lower)) return 'CSV';
  return 'unknown';
}

/** The manifest the backend reads: every file once, each with its own split. */
export function buildManifest(entries: Array<{ file: File; split: string }>, csv: CsvOptions | null, displayName: string): UploadManifest {
  const manifest: UploadManifest = { files: entries.map((e) => ({ name: e.file.name, split: e.split.trim() })) };
  if (csv && entries.some((e) => formatFromName(e.file.name) === 'CSV')) manifest.csv = csv;
  if (displayName.trim()) manifest.display_name = displayName.trim();
  return manifest;
}

const field = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

export function UploadCard({ onOutcome }: { onOutcome?: (outcome: ImportOutcome) => void }) {
  const meta = useSourcesStore((s) => s.meta);
  const uploadFiles = useSourcesStore((s) => s.uploadFiles);
  const [entries, setEntries] = useState<Array<{ file: File; split: string }>>([]);
  const [csv, setCsv] = useState<CsvOptions | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const limit = meta?.limits.upload_max_bytes ?? null;
  const options = csv ?? meta?.csv_defaults ?? null;

  const add = (files: FileList | File[]) => {
    setError(null);
    setEntries((prev) => {
      const next = [...prev];
      for (const file of Array.from(files)) {
        if (next.some((e) => e.file.name === file.name)) continue;
        const used = new Set(next.map((e) => e.split));
        const split = SUGGESTED.find((s) => !used.has(s)) ?? `split_${next.length + 1}`;
        next.push({ file, split });
      }
      return next;
    });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer?.files?.length) add(e.dataTransfer.files);
  };

  const oversized = limit === null ? [] : entries.filter((e) => e.file.size > limit);
  const splits = entries.map((e) => e.split.trim());
  const duplicate = new Set(splits).size !== splits.length || splits.some((s) => !s);

  const send = async () => {
    setBusy(true);
    setError(null);
    const outcome = await uploadFiles(entries.map((e) => e.file), buildManifest(entries, options, displayName));
    setBusy(false);
    if (outcome.kind === 'refused') {
      setError(outcome.message); // 413 upload_too_large names the file and the limit
    } else {
      setEntries([]);
      setDisplayName('');
    }
    onOutcome?.(outcome);
  };

  return (
    <section aria-labelledby="upload-title" className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
      <h2 id="upload-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">Upload a file</h2>
      <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
        Parquet, JSONL or CSV; each file becomes one split of one source.{limit !== null && ` Up to ${formatBytes(limit)} per file (Settings → Storage).`}
      </p>
      <label
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        data-testid="upload-drop"
        className={`flex flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-4 py-6 text-sm cursor-pointer focus-within:ring-2 focus-within:ring-indigo-500 ${dragging ? 'border-indigo-500 bg-indigo-500/5' : 'border-slate-300 dark:border-slate-600'}`}
      >
        <Upload className="w-5 h-5 text-slate-400" aria-hidden="true" />
        <span className="text-slate-600 dark:text-slate-300">Drop files here, or choose them</span>
        <input type="file" multiple accept=".parquet,.jsonl,.ndjson,.json,.csv,.tsv,.txt" className="sr-only" aria-label="Choose files to upload" onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ''; }} />
      </label>
      {entries.length > 0 && (
        <ul className="mt-3 space-y-2" data-testid="upload-files">
          {entries.map((entry, i) => (
            <li key={entry.file.name} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono truncate max-w-[14rem]" title={entry.file.name}>{entry.file.name}</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">{formatBytes(entry.file.size)} · {formatFromName(entry.file.name)}</span>
              <label className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
                Split
                <input aria-label={`Split name for ${entry.file.name}`} value={entry.split} onChange={(e) => setEntries((prev) => prev.map((p, j) => (j === i ? { ...p, split: e.target.value } : p)))} className={`${field} font-mono w-28`} />
              </label>
              {limit !== null && entry.file.size > limit && <span className="text-xs text-red-700 dark:text-red-400">Over the {formatBytes(limit)} limit: split the file or raise the limit in Settings.</span>}
              <button type="button" aria-label={`Remove ${entry.file.name}`} onClick={() => setEntries((prev) => prev.filter((_, j) => j !== i))} className="p-1 rounded text-slate-400 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><X className="w-3 h-3" /></button>
            </li>
          ))}
        </ul>
      )}
      {entries.length > 0 && (
        <label className="block mt-3 text-xs text-slate-500 dark:text-slate-400">
          Display name (optional; the first file name if empty)
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={`${field} w-full mt-1`} />
        </label>
      )}
      {options && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-slate-600 dark:text-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded">CSV options</summary>
          <div className="grid gap-2 sm:grid-cols-3 mt-2 text-xs">
            <label className="flex flex-col gap-1">Delimiter<input aria-label="CSV delimiter" maxLength={1} value={options.delimiter} onChange={(e) => setCsv({ ...options, delimiter: e.target.value })} className={`${field} font-mono`} /></label>
            <label className="flex flex-col gap-1">Quote<input aria-label="CSV quote character" maxLength={1} value={options.quote} onChange={(e) => setCsv({ ...options, quote: e.target.value })} className={`${field} font-mono`} /></label>
            <label className="flex flex-col gap-1">Encoding<input aria-label="CSV encoding" value={options.encoding} onChange={(e) => setCsv({ ...options, encoding: e.target.value })} className={field} /></label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={options.header} onChange={(e) => setCsv({ ...options, header: e.target.checked })} />First row is a header</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={options.type_mode === 'text'} onChange={(e) => setCsv({ ...options, type_mode: e.target.checked ? 'text' : 'infer' })} />Read every column as text</label>
          </div>
        </details>
      )}
      {error && <p role="alert" className="text-sm mt-3 text-red-700 dark:text-red-400">{error}</p>}
      {duplicate && entries.length > 0 && <p className="text-xs mt-2 text-amber-700 dark:text-amber-400">Give each file its own, non-empty split name.</p>}
      <div className="flex justify-end mt-3">
        <Button leftIcon={<Upload className="w-4 h-4" />} onClick={() => void send()} disabled={busy || entries.length === 0 || duplicate || oversized.length > 0}>
          {busy ? 'Uploading…' : entries.length ? `Upload ${formatCount(entries.length, 'file')}` : 'Upload files'}
        </Button>
      </div>
    </section>
  );
}

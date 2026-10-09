// Draw a version's audit sample: 50 to 100 rows, default 100 (R-03.40, T-26), stratified by
// effective label and probability band. A new draw supersedes an audit in progress.
import { useState } from 'react';

import { useReviewStore } from '@/stores/reviewStore';

export const AUDIT_MIN = 50;
export const AUDIT_MAX = 100;

export function DrawAuditDialog({ onClose }: { onClose: () => void }) {
  const drawAudit = useReviewStore((s) => s.drawAudit);
  const [versionId, setVersionId] = useState('');
  const [size, setSize] = useState('100');
  const n = Number(size);
  const inRange = Number.isInteger(n) && n >= AUDIT_MIN && n <= AUDIT_MAX;
  return (
    <div role="dialog" aria-modal="true" aria-label="Draw an audit" className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4" data-testid="draw-audit-dialog">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-slate-900 p-5 space-y-3">
        <h3 className="text-lg font-semibold">Draw an audit</h3>
        <p className="text-xs text-slate-600 dark:text-slate-300">A public push needs a completed audit: every sampled row decided by you. Agent decisions do not count.</p>
        <label className="block text-xs">Version ID
          <input className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" value={versionId} onChange={(e) => setVersionId(e.target.value)} />
        </label>
        <label className="block text-xs">Rows ({AUDIT_MIN} to {AUDIT_MAX})
          <input aria-label="Audit size" className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" value={size} onChange={(e) => setSize(e.target.value)} />
        </label>
        {!inRange && <p className="text-xs text-red-700 dark:text-red-300" data-testid="audit-size-hint">Choose {AUDIT_MIN} to {AUDIT_MAX} rows; 100 is the default.</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={onClose}>Close</button>
          <button type="button" disabled={!versionId || !inRange} className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
            onClick={async () => { if (await drawAudit(versionId, n)) onClose(); }}>
            Draw audit sample
          </button>
        </div>
      </div>
    </div>
  );
}

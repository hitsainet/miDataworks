// Create a label-review queue (from a label run) or a calibration-labeling queue (model output
// hidden by default, T-25).
import { useState } from 'react';

import { useReviewStore } from '@/stores/reviewStore';

const input = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function CreateQueueDialog({ onClose }: { onClose: () => void }) {
  const createQueue = useReviewStore((s) => s.createQueue);
  const [kind, setKind] = useState<'label_review' | 'calibration_labeling'>('label_review');
  const [runId, setRunId] = useState('');
  const [versionId, setVersionId] = useState('');
  const [question, setQuestion] = useState('');
  const [labels, setLabels] = useState('humorous, not_humorous');
  const [size, setSize] = useState('100');
  const [show, setShow] = useState(false);
  const labelSet = labels.split(',').map((l) => l.trim()).filter(Boolean);
  const ready = kind === 'label_review' ? Boolean(runId) : Boolean(versionId && question && labelSet.length >= 2);
  return (
    <div role="dialog" aria-modal="true" aria-label="Create a review queue" className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4" data-testid="create-queue-dialog">
      <div className="w-full max-w-lg rounded-xl bg-white dark:bg-slate-900 p-5 space-y-3">
        <h3 className="text-lg font-semibold">Create a review queue</h3>
        <label className="block text-xs">Kind
          <select className={input} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="label_review">Review a label run's labels</option>
            <option value="calibration_labeling">Label rows for a calibration set</option>
          </select>
        </label>
        {kind === 'label_review' ? (
          <label className="block text-xs">Label run ID<input className={input} value={runId} onChange={(e) => setRunId(e.target.value)} /></label>
        ) : (
          <>
            <label className="block text-xs">Version ID<input className={input} value={versionId} onChange={(e) => setVersionId(e.target.value)} /></label>
            <label className="block text-xs">Question<input className={input} value={question} onChange={(e) => setQuestion(e.target.value)} /></label>
            <label className="block text-xs">Labels, positive first<input className={input} value={labels} onChange={(e) => setLabels(e.target.value)} /></label>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />Show model output to reviewers (recorded on every decision)</label>
          </>
        )}
        <label className="block text-xs">Rows to sample<input className={input} value={size} onChange={(e) => setSize(e.target.value)} /></label>
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={onClose}>Close</button>
          <button type="button" disabled={!ready} className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
            onClick={async () => {
              const body = kind === 'label_review'
                ? { kind, label_run_id: runId, size: Number(size) }
                : { kind, version_id: versionId, question, label_set: labelSet, size: Number(size), show_model_output: show };
              if (await createQueue(body)) onClose();
            }}>
            Create queue
          </button>
        </div>
      </div>
    </div>
  );
}

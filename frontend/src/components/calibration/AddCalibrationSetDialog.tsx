// "Add a calibration set" (FR-006.2): from an imported dataset (column mapping, previewed on change,
// debounced 400 ms) or from a calibration-labeling review queue (operator decisions only).
import { useEffect, useState } from 'react';

import { useCalibrationStore } from '@/stores/calibrationStore';
import { useReviewStore } from '@/stores/reviewStore';

import { EMPTY_DRAFT, MappingForm, toMapping, type MappingDraft } from './MappingForm';
import { MappingPreview } from './MappingPreview';

export const PREVIEW_DEBOUNCE_MS = 400;
const input = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function AddCalibrationSetDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'imported' | 'review'>('imported');
  const [versionId, setVersionId] = useState('');
  const [question, setQuestion] = useState('');
  const [labels, setLabels] = useState('humorous, not_humorous');
  const [draft, setDraft] = useState<MappingDraft>(EMPTY_DRAFT);
  const [queueId, setQueueId] = useState('');
  const preview = useCalibrationStore((s) => s.preview);
  const previewError = useCalibrationStore((s) => s.previewError);
  const previewMapping = useCalibrationStore((s) => s.previewMapping);
  const importSet = useCalibrationStore((s) => s.importSet);
  const buildFromReview = useCalibrationStore((s) => s.buildFromReview);
  const queues = useReviewStore((s) => s.queues).filter((q) => q.kind === 'calibration_labeling');

  const mapping = toMapping(draft);
  const labelSet = labels.split(',').map((l) => l.trim()).filter(Boolean);
  const body = mapping && versionId && question && labelSet.length >= 2 ? { version_id: versionId, question, label_set: labelSet, mapping } : null;
  const key = body ? JSON.stringify(body) : '';

  useEffect(() => {
    if (!key) return undefined;
    const timer = setTimeout(() => void previewMapping(JSON.parse(key)), PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key, previewMapping]);

  return (
    <div role="dialog" aria-modal="true" aria-label="Add a calibration set" className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4" data-testid="add-set-dialog">
      <div className="w-full max-w-2xl rounded-xl bg-white dark:bg-slate-900 p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <h3 className="text-lg font-semibold">Add a calibration set</h3>
        <div role="tablist" className="flex gap-2">
          {(['imported', 'review'] as const).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 ${tab === t ? 'bg-indigo-500 text-white' : 'border border-slate-300 dark:border-slate-600'}`}>
              {t === 'imported' ? 'From an imported dataset' : 'From a review queue'}
            </button>
          ))}
        </div>
        {tab === 'imported' ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-xs">Version ID<input className={input} value={versionId} onChange={(e) => setVersionId(e.target.value)} /></label>
              <label className="block text-xs">Labels, positive first<input className={input} value={labels} onChange={(e) => setLabels(e.target.value)} /></label>
              <label className="col-span-2 block text-xs">Question (the exact text the labeler is asked)<input className={input} value={question} onChange={(e) => setQuestion(e.target.value)} /></label>
            </div>
            <MappingForm draft={draft} onChange={setDraft} />
            <MappingPreview preview={body ? preview : null} error={body ? previewError : null} />
          </>
        ) : (
          <label className="block text-xs">
            Calibration-labeling queue (only operator decisions become human labels)
            <select className={input} value={queueId} onChange={(e) => setQueueId(e.target.value)}>
              <option value="">Choose a queue</option>
              {queues.map((q) => <option key={q.id} value={q.id}>{q.question} · {q.decided}/{q.items} decided</option>)}
            </select>
          </label>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={onClose}>Close</button>
          <button type="button" disabled={tab === 'imported' ? !body : !queueId}
            className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
            onClick={async () => {
              const created = tab === 'imported' && body ? await importSet(body) : await buildFromReview(queueId);
              if (created) onClose();
            }}>
            Create calibration set
          </button>
        </div>
      </div>
    </div>
  );
}

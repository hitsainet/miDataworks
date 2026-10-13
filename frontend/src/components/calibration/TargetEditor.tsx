// The operator's gate target for a question (FR-006.19): the AUROC CI lower bound a labeler must
// reach. Without one the default is C3 (0.70) when no valid ceiling exists. Agents change targets
// through the gated tool and wait for approval (S3-08); this form is the operator's.
import { useEffect, useState } from 'react';

import { useCalibrationStore } from '@/stores/calibrationStore';

export function TargetEditor({ question, questionHash }: { question: string; questionHash: string }) {
  const targets = useCalibrationStore((s) => s.targets[questionHash]);
  const fetchTargets = useCalibrationStore((s) => s.fetchTargets);
  const setTarget = useCalibrationStore((s) => s.setTarget);
  const [value, setValue] = useState('');
  useEffect(() => void fetchTargets(questionHash), [fetchTargets, questionHash]);
  const current = targets?.current;
  const number = Number(value);
  const valid = value !== '' && number > 0.5 && number < 1;
  return (
    <div className="flex flex-wrap items-end gap-2 text-xs" data-testid="target-editor">
      <span className="text-slate-600 dark:text-slate-300">
        Gate target: {current ? `${current.target.toFixed(3)} (set by ${current.set_by}${current.approved_by ? `, approved by ${current.approved_by}` : ''})` : 'default (C3) — CI lower bound 0.70 when no rater ceiling exists'}
      </span>
      <label className="flex items-center gap-1">
        New target
        <input aria-label="New gate target" className="w-20 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-1 py-0.5 focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none" value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <button type="button" disabled={!valid} className="rounded border border-slate-300 dark:border-slate-600 px-2 py-0.5 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
        onClick={async () => { if (await setTarget(question, questionHash, number)) setValue(''); }}>
        Set target
      </button>
    </div>
  );
}

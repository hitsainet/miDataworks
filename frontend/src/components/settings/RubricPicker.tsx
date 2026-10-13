// The judge card's rubric list (FR-005.51).
import { useEffect } from 'react';

import { useLabelingStore } from '@/stores/labelingStore';

export function RubricPicker() {
  const rubrics = useLabelingStore((s) => s.rubrics);
  const fetchRubrics = useLabelingStore((s) => s.fetchRubrics);
  useEffect(() => {
    void fetchRubrics();
  }, [fetchRubrics]);
  return (
    <div className="mb-4 text-xs" data-testid="rubric-picker">
      <div className="font-medium text-slate-700 dark:text-slate-300 mb-1">Rubrics</div>
      <ul className="space-y-0.5">
        {rubrics.map((r) => <li key={r.id} className="font-mono">{r.ref} · {r.style}{r.used ? ' · used, fixed' : ''}</li>)}
        {rubrics.length === 0 && <li className="text-slate-500 dark:text-slate-400">No rubrics yet. Write or import one on Label runs, under Rubrics.</li>}
      </ul>
    </div>
  );
}

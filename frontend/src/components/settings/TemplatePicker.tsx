// The classifier card's decision-template list (FR-005.51): which templates exist, the model each
// is bound to, and whether a run has used it (then it can no longer change).
import { useEffect } from 'react';

import { useLabelingStore } from '@/stores/labelingStore';

export function TemplatePicker() {
  const templates = useLabelingStore((s) => s.templates);
  const fetchTemplates = useLabelingStore((s) => s.fetchTemplates);
  useEffect(() => {
    void fetchTemplates();
  }, [fetchTemplates]);
  return (
    <div className="mb-4 text-xs" data-testid="template-picker">
      <div className="font-medium text-slate-700 dark:text-slate-300 mb-1">Decision templates</div>
      <ul className="space-y-0.5">
        {templates.map((t) => (
          <li key={t.id} className="font-mono">{t.ref} · {t.bound_model_id ?? 'any model'}{t.used ? ' · used, fixed' : ''}</li>
        ))}
        {templates.length === 0 && <li className="text-slate-500 dark:text-slate-400">No templates yet.</li>}
      </ul>
    </div>
  );
}

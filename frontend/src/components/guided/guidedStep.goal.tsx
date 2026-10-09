// Feature 002's Goal step (FR-002.48): what will train on the dataset. Choosing a target type sets
// the default content columns; it creates the dataset, or changes its target type while it has no
// version (the backend refuses once one exists).
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useDraftsStore } from '@/stores/draftsStore';

import type { GuidedStepPanel, GuidedStepProps } from './guidedSteps';

function GoalStep({ draft, onAdvance }: GuidedStepProps) {
  const meta = useDatasetsStore((s) => s.meta);
  const error = useDatasetsStore((s) => s.error);
  const fetchMeta = useDatasetsStore((s) => s.fetchMeta);
  const createDataset = useDatasetsStore((s) => s.createDataset);
  const setTargetType = useDatasetsStore((s) => s.setTargetType);
  const updateDraft = useDraftsStore((s) => s.updateDraft);
  const choices = (draft.flow_state.choices ?? {}) as { target_type?: string; dataset_name?: string };
  const [target, setTarget] = useState(choices.target_type ?? 'detector');
  const [name, setName] = useState(choices.dataset_name ?? '');

  useEffect(() => {
    void fetchMeta();
  }, [fetchMeta]);

  const commit = async () => {
    const dataset = draft.dataset_id ? await setTargetType(draft.dataset_id, target) : await createDataset(name.trim(), target);
    if (!dataset) return;
    updateDraft({ dataset_id: dataset.id, flow_state: { ...draft.flow_state, choices: { ...choices, target_type: target, dataset_name: dataset.name } } });
    onAdvance();
  };

  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  return (
    <div>
      <h2 className="font-semibold mb-1">What will train on this dataset?</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">The goal sets which columns identify a row. You can change it until the first version is built.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {!draft.dataset_id && (
          <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Dataset name (lower case, digits and hyphens)</span>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} className={`${field} font-mono`} />
          </label>
        )}
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Goal</span>
          <select value={target} onChange={(e) => setTarget(e.target.value)} className={field}>
            {(meta?.target_types ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">
        Rows are keyed on: {(meta?.default_content_columns[target] ?? []).join(' or ') || 'the text column the import detected'}.
      </p>
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300 mt-2">{error}</p>}
      <div className="flex justify-end mt-4">
        <Button onClick={() => void commit()} disabled={!draft.dataset_id && !name.trim()}>{draft.dataset_id ? 'Save the goal' : 'Create the dataset'}</Button>
      </div>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'goal', order: 0, Component: GoalStep };
export default panel;

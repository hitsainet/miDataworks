// One recipe step in the editor: operator, version, parameters, and its own validation errors.
// Feature 003's schema form (FR-003.23) replaces the JSON parameters box when it lands; until then
// the parameters are edited as JSON and validated by the backend exactly as the form would be.
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react';

import type { StepError } from '@/types/recipes';

export interface EditableStep {
  operator: string;
  version: string;
  params: string;
  label: string;
}

export function StepRow({
  step,
  index,
  count,
  errors,
  onChange,
  onMove,
  onRemove,
}: {
  step: EditableStep;
  index: number;
  count: number;
  errors: StepError[];
  onChange: (step: EditableStep) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500';
  return (
    <li className={`rounded-lg border p-3 ${errors.length ? 'border-red-400 dark:border-red-500/60' : 'border-slate-200 dark:border-slate-700'}`} data-testid="step-row">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono text-slate-500">Step {index + 1}</span>
        <span className="flex gap-1">
          <button type="button" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => onMove(-1)} className="p-1 rounded disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-indigo-500"><ArrowUp className="w-4 h-4" aria-hidden="true" /></button>
          <button type="button" aria-label={`Move step ${index + 1} down`} disabled={index === count - 1} onClick={() => onMove(1)} className="p-1 rounded disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-indigo-500"><ArrowDown className="w-4 h-4" aria-hidden="true" /></button>
          <button type="button" aria-label={`Remove step ${index + 1}`} onClick={onRemove} className="p-1 rounded text-red-600 dark:text-red-400 focus-visible:ring-2 focus-visible:ring-indigo-500"><Trash2 className="w-4 h-4" aria-hidden="true" /></button>
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Operator</span><input className={`${field} font-mono`} value={step.operator} onChange={(e) => onChange({ ...step, operator: e.target.value })} /></label>
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Version</span><input className={`${field} font-mono`} value={step.version} onChange={(e) => onChange({ ...step, version: e.target.value })} /></label>
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Label (not hashed)</span><input className={field} value={step.label} onChange={(e) => onChange({ ...step, label: e.target.value })} /></label>
      </div>
      <label className="block text-xs mt-2">
        <span className="block text-slate-500 dark:text-slate-400 mb-1">Parameters (JSON)</span>
        <textarea className={`${field} font-mono h-20`} value={step.params} onChange={(e) => onChange({ ...step, params: e.target.value })} />
      </label>
      {errors.length > 0 && (
        <ul className="mt-2 text-xs text-red-700 dark:text-red-300 space-y-0.5" data-testid="step-errors">
          {errors.map((e, i) => <li key={i}>{e.message}</li>)}
        </ul>
      )}
    </li>
  );
}

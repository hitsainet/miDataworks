// One recipe step in the editor: operator, version, parameters, and its own validation errors.
// The operator and its version are chosen from feature 003's registry (novice walk 2026-10-19,
// finding 7), never typed from memory; choosing an operator picks its newest allowed version and
// its settings form appears at once. Used by the Recipes editor and the guided Curate step.
// The parameters are edited with feature 003's schema form (FR-003.23; 002 FTASKS 21.4), rendered
// from the step's operator manifest. The JSON box stays as the fallback, and says why, when the
// operator or its manifest cannot be read, when the stored parameters are not a JSON object, or when
// the operator asks for it ("Edit as JSON"). The backend validates either way.
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { operatorsApi } from '@/api/operators';
import { SchemaForm } from '@/components/operators/SchemaForm';

import { useOperatorChoices } from './operatorChoices';
import type { ParamsSchema } from '@/types/operators';
import type { StepError } from '@/types/recipes';

/** The step's operator manifest schema: loading, found, or why there is none. */
type SchemaState = { status: 'none'; reason: string } | { status: 'loading' } | { status: 'found'; schema: ParamsSchema };

function useParamsSchema(operator: string, version: string): SchemaState {
  const name = operator.trim();
  const ver = version.trim();
  const ref = name && ver ? `${name}@${ver}` : '';
  // The answer for one operator@version; a different ref than the current one means "loading".
  const [answer, setAnswer] = useState<{ ref: string; state: SchemaState } | null>(null);
  useEffect(() => {
    if (!ref) return;
    let live = true;
    operatorsApi
      .get(name, ver)
      .then((entry) => {
        if (!live) return;
        const schema = entry.manifest?.params_schema;
        setAnswer({
          ref,
          state: schema ? { status: 'found', schema } : { status: 'none', reason: `${name} ${ver} has no readable manifest${entry.error ? `: ${entry.error}` : ''}.` },
        });
      })
      .catch((e: unknown) => {
        if (live) setAnswer({ ref, state: { status: 'none', reason: `Settings form unavailable: ${e instanceof Error ? e.message : String(e)}` } });
      });
    return () => {
      live = false;
    };
  }, [ref, name, ver]);
  if (!ref) return { status: 'none', reason: 'Choose an operator to see its settings.' };
  return answer?.ref === ref ? answer.state : { status: 'loading' };
}

function asObject(params: string): Record<string, unknown> | null {
  try {
    const value: unknown = params.trim() ? JSON.parse(params) : {};
    return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

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
  const schema = useParamsSchema(step.operator, step.version);
  const catalogue = useOperatorChoices();
  const choices = catalogue.status === 'ready' ? catalogue.choices : [];
  const chosen = choices.find((c) => c.name === step.operator.trim());
  const versions = chosen?.versions ?? [];
  const pickOperator = (name: string) => {
    const next = choices.find((c) => c.name === name);
    onChange({ ...step, operator: name, version: next?.current?.version ?? next?.versions[0]?.version ?? '', params: '{}' });
  };
  const [asJson, setAsJson] = useState(false);
  const values = asObject(step.params);
  const useForm = schema.status === 'found' && values !== null && !asJson;
  const field = 'w-full rounded-sm border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus:outline-hidden focus-visible:ring-2 focus-visible:ring-indigo-500';
  return (
    <li className={`rounded-lg border p-3 ${errors.length ? 'border-red-400 dark:border-red-500/60' : 'border-slate-200 dark:border-slate-700'}`} data-testid="step-row">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono text-slate-500">Step {index + 1}</span>
        <span className="flex gap-1">
          <button type="button" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => onMove(-1)} className="p-1 rounded-sm disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-indigo-500"><ArrowUp className="w-4 h-4" aria-hidden="true" /></button>
          <button type="button" aria-label={`Move step ${index + 1} down`} disabled={index === count - 1} onClick={() => onMove(1)} className="p-1 rounded-sm disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-indigo-500"><ArrowDown className="w-4 h-4" aria-hidden="true" /></button>
          <button type="button" aria-label={`Remove step ${index + 1}`} onClick={onRemove} className="p-1 rounded-sm text-red-600 dark:text-red-400 focus-visible:ring-2 focus-visible:ring-indigo-500"><Trash2 className="w-4 h-4" aria-hidden="true" /></button>
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Operator</span>
          <select aria-label={`Operator for step ${index + 1}`} className={`${field} font-mono`} value={step.operator} onChange={(e) => pickOperator(e.target.value)}>
            <option value="">{catalogue.status === 'loading' ? 'Reading the operators…' : catalogue.status === 'error' ? `Operators unavailable: ${catalogue.message}` : 'Choose an operator'}</option>
            {step.operator && !chosen && <option value={step.operator}>{step.operator} (not installed)</option>}
            {choices.map((c) => (
              <option key={c.name} value={c.name} disabled={!c.current && c.name !== step.operator}>
                {c.name}{c.current ? '' : ' (not allowed)'}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Version</span>
          <select aria-label={`Version for step ${index + 1}`} className={`${field} font-mono`} value={step.version} disabled={!step.operator} onChange={(e) => onChange({ ...step, version: e.target.value })}>
            {!step.operator && <option value="">Choose an operator first</option>}
            {step.version && !versions.some((v) => v.version === step.version) && <option value={step.version}>{step.version} (not installed)</option>}
            {versions.map((v) => <option key={v.version} value={v.version}>{v.version}{v.state === 'allowed' ? '' : ` (${v.state.replace(/_/g, ' ')})`}</option>)}
          </select>
        </label>
        <label className="text-xs"><span className="block text-slate-500 dark:text-slate-400 mb-1">Step note (optional, not part of the recipe hash)</span><input aria-label={`Note for step ${index + 1}`} className={field} value={step.label} onChange={(e) => onChange({ ...step, label: e.target.value })} /></label>
      </div>
      {chosen?.current?.description && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400" data-testid="step-description">{chosen.current.description}</p>}
      <div className="mt-2 flex items-center justify-between text-xs">
        <span className="text-slate-500 dark:text-slate-400">Settings</span>
        {schema.status === 'found' && values !== null && (
          <button type="button" className="underline rounded-sm focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => setAsJson(!asJson)} data-testid="params-mode">
            {asJson ? 'Edit with the form' : 'Edit as JSON'}
          </button>
        )}
      </div>
      {!step.operator.trim() ? (
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400" data-testid="params-choose-first">Choose an operator to see its settings.</p>
      ) : useForm && schema.status === 'found' && values !== null ? (
        <div className="mt-1" data-testid="step-schema-form">
          <SchemaForm schema={schema.schema} values={values} onChange={(next) => onChange({ ...step, params: JSON.stringify(next) })} />
        </div>
      ) : (
        <div className="block text-xs mt-1">
          <textarea aria-label="Parameters (JSON)" className={`${field} font-mono h-20`} value={step.params} onChange={(e) => onChange({ ...step, params: e.target.value })} />
          {!asJson && (
            <span className="mt-1 block text-slate-500 dark:text-slate-400" data-testid="params-json-reason">
              {schema.status === 'loading' ? 'Reading the operator\'s settings…' : schema.status === 'none' ? schema.reason : 'The parameters are not a JSON object, so the form cannot show them; fix them here.'}
            </span>
          )}
        </div>
      )}
      {errors.length > 0 && (
        <ul className="mt-2 text-xs text-red-700 dark:text-red-300 stack-y-0.5" data-testid="step-errors">
          {errors.map((e, i) => <li key={i}>{e.message}</li>)}
        </ul>
      )}
    </li>
  );
}

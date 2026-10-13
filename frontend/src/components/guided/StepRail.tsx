// The guided flow's step list: done, current and to-do, each named in words as well as shape.
import { Check } from 'lucide-react';

export function StepRail({ steps, current, onSelect }: { steps: string[]; current: string; onSelect: (step: string) => void }) {
  const at = Math.max(0, steps.indexOf(current));
  return (
    <ol className="space-y-3" aria-label="Steps" data-testid="step-rail">
      {steps.map((step, i) => {
        const state = i < at ? 'done' : i === at ? 'current' : 'to do';
        return (
          <li key={step}>
            <button type="button" onClick={() => onSelect(step)} aria-current={i === at ? 'step' : undefined}
              className="flex items-center gap-3 w-full text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
              <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${state === 'done' ? 'bg-green-500 text-white' : state === 'current' ? 'bg-indigo-500 text-white' : 'bg-slate-200 dark:bg-slate-700 text-slate-500'}`}>
                {state === 'done' ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : i + 1}
              </span>
              <span>
                <span className={`block text-sm font-medium capitalize ${state === 'to do' ? 'text-slate-500 dark:text-slate-400' : ''}`}>{step}</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">{state}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

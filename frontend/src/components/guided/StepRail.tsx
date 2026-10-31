// The guided flow's step list, each named in words as well as shape. A step reads "done" only when
// what it produces exists (journey.stepStatuses), never because the user moved past it (novice walk
// 2026-10-19, finding 1: Profile read "done" with no version to profile).
import { Check } from 'lucide-react';

import type { StepStatus } from './journey';

export function StepRail({ steps, current, onSelect, statuses = {} }: { steps: string[]; current: string; onSelect: (step: string) => void; statuses?: Record<string, StepStatus> }) {
  const at = Math.max(0, steps.indexOf(current));
  return (
    <ol className="stack-y-3" aria-label="Steps" data-testid="step-rail">
      {steps.map((step, i) => {
        const status = statuses[step];
        const state = i === at ? 'current' : status?.done ? 'done' : i < at ? 'not done' : 'to do';
        const words = state === 'not done' && status?.note ? `not done: ${status.note}` : state;
        return (
          <li key={step} data-state={state}>
            <button type="button" onClick={() => onSelect(step)} aria-current={i === at ? 'step' : undefined}
              className="flex items-center gap-3 w-full text-left rounded-control focus:outline-hidden focus-visible:ring-2 focus-visible:ring-indigo-500">
              <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${state === 'done' ? 'bg-green-500 text-white' : state === 'current' ? 'bg-indigo-500 text-white' : state === 'not done' ? 'bg-amber-100 dark:bg-amber-500/20 text-amber-800 dark:text-amber-300' : 'bg-slate-200 dark:bg-slate-700 text-slate-500'}`}>
                {state === 'done' ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : i + 1}
              </span>
              <span>
                <span className={`block text-sm font-medium capitalize ${state === 'to do' ? 'text-slate-500 dark:text-slate-400' : ''}`}>{step}</span>
                <span className={`block text-xs ${state === 'not done' ? 'text-amber-700 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}`}>{words}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

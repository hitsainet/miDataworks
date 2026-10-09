// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/components/common/Select.tsx @ 0efff20
// Mode: copy (docs/REUSE.md), recoloured to indigo with light-mode pairs; checked for React 19-only APIs (none: forwardRef only).
import { forwardRef, useId } from 'react';
import type { SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';

interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  helper?: string;
  options: SelectOption[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      label,
      error,
      helper,
      options,
      placeholder,
      className = '',
      id,
      ...props
    },
    ref
  ) => {
    const generatedId = useId();
    const selectId = id || generatedId;

    return (
      <div className="w-full">
        {label && (
          <label
            htmlFor={selectId}
            className="block text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5"
          >
            {label}
          </label>
        )}
        <div className="relative">
          <select
            ref={ref}
            id={selectId}
            className={`
              w-full px-3.5 py-2.5 pr-10
              bg-slate-50 dark:bg-slate-800/50 border rounded-lg
              text-slate-800 dark:text-slate-200 text-sm
              appearance-none cursor-pointer
              transition-colors duration-200
              focus:outline-none focus:border-indigo-400/50
              ${error ? 'border-red-500/50' : 'border-slate-300 dark:border-slate-600/50'}
              ${className}
            `}
            {...props}
          >
            {placeholder && (
              <option value="" disabled>
                {placeholder}
              </option>
            )}
            {options.map((option) => (
              <option
                key={option.value}
                value={option.value}
                disabled={option.disabled}
              >
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
        </div>
        {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
        {helper && !error && (
          <p className="mt-1 text-xs text-slate-500">{helper}</p>
        )}
      </div>
    );
  }
);

Select.displayName = 'Select';

export default Select;

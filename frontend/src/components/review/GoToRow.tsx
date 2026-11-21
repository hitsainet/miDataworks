// Jump to a row by its number (operator, 2026-11-02): rows to change are scattered through a long
// queue, and j and k pass every row between them. Only digits count; a number outside the queue says
// so and stays on the row on screen.
import { useState } from 'react';
import type { FormEvent } from 'react';

export function GoToRow({ total, disabled, onGo }: { total: number; disabled: boolean; onGo: (row: number) => boolean }) {
  const [value, setValue] = useState('');
  const [refused, setRefused] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (disabled) return;
    const typed = value.trim();
    if (/^\d+$/.test(typed) && onGo(Number(typed))) {
      setValue('');
      setRefused(false);
    } else {
      setRefused(true);
    }
  };

  return (
    <form className="flex flex-wrap items-center gap-1.5" onSubmit={submit} data-testid="go-to-row">
      <label className="flex items-center gap-1.5">
        Go to row
        <input
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setRefused(false);
          }}
          aria-invalid={refused}
          className="w-14 rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-1.5 py-0.5 text-xs outline-hidden focus-visible:ring-2 focus-visible:ring-indigo-500"
        />
      </label>
      <button type="submit" disabled={disabled} className="rounded-control border border-slate-300 dark:border-slate-600 px-2 py-0.5 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500">
        Go
      </button>
      {refused && (
        <span role="alert" className="text-red-700 dark:text-red-300">
          Enter a row from 1 to {total}.
        </span>
      )}
    </form>
  );
}

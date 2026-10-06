import type { ReactNode } from 'react';

/** Page title (24 px semibold, sentence case) and one-line subtitle, as the mockup's PageHead. */
export function PageHead({ title, subtitle, right }: { title: string; subtitle: string; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{title}</h1>
        <div className="mt-1 text-slate-500 dark:text-slate-400">{subtitle}</div>
      </div>
      {right}
    </div>
  );
}

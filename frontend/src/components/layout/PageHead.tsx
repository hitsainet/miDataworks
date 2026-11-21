import type { ReactNode } from 'react';

import { HelpLink } from '@/components/help/HelpLink';

/** Page title (24 px semibold, sentence case) and one-line subtitle, as the mockup's PageHead.
 * ``help`` names a topic of the Help screen; the link sits under the subtitle (finding 14). */
export function PageHead({ title, subtitle, right, help }: { title: string; subtitle: string; right?: ReactNode; help?: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{title}</h1>
        <div className="mt-1 text-slate-500 dark:text-slate-400">{subtitle}</div>
        {help && <div className="mt-1"><HelpLink topic={help} /></div>}
      </div>
      {right}
    </div>
  );
}

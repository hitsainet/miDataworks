// A link into the public manual (config/manual.ts). Opens in a new tab: the manual is a separate
// site, and leaving the application mid-step would lose the user's place.
import { BookOpen } from 'lucide-react';

import { manualUrl } from '@/config/manual';

export function ManualLink({ page, label, testId }: { page: string; label: string; testId?: string }) {
  return (
    <a
      href={manualUrl(page)}
      target="_blank"
      rel="noopener noreferrer"
      data-testid={testId}
      className="inline-flex items-center gap-1 text-xs text-indigo-700 dark:text-indigo-300 hover:underline rounded-control focus-visible:ring-2 focus-visible:ring-indigo-500"
    >
      <BookOpen className="w-3.5 h-3.5" aria-hidden="true" />
      {label}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

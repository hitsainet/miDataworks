// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/components/common/EmptyState.tsx @ 0efff20
// Mode: copy (docs/REUSE.md), recoloured to indigo with light-mode pairs; checked for React 19-only APIs (none: forwardRef only).
import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className = '',
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center py-12 px-4 text-center ${className}`}
    >
      <div className="w-12 h-12 bg-slate-100 dark:bg-slate-800 rounded-xl flex items-center justify-center mb-4">
        {icon || <Inbox className="w-6 h-6 text-slate-500" />}
      </div>
      <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-200 mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-slate-500 max-w-sm mb-4">{description}</p>
      )}
      {action}
    </div>
  );
}

export default EmptyState;

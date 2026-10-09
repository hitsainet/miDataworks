// The entry-point allowlist (FR-003.11; P-09). Each change needs a reason and records the Settings
// operator name; agents cannot change it (the API refuses), so this screen is the only place.
import { useState } from 'react';

import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import type { AllowlistItem } from '@/types/operators';

export function AllowlistSection({
  items,
  error,
  onChange,
}: {
  items: AllowlistItem[];
  error: string | null;
  onChange: (action: 'allow' | 'revoke', item: AllowlistItem, reason: string) => Promise<boolean>;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string | null>(null);
  if (!items.length) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="allowlist-empty">
        No third-party operator package is installed. Native, Data-Juicer and Data Designer operators are allowed by code review.
      </p>
    );
  }
  return (
    <div className="space-y-3" data-testid="allowlist">
      {error ? <p role="alert" className="text-sm text-red-500">{error}</p> : null}
      {items.map((item) => {
        const id = `${item.distribution}|${item.distribution_version}|${item.entry_point}`;
        const reason = reasons[id] ?? '';
        const act = async (action: 'allow' | 'revoke') => {
          if (!reason.trim()) {
            setMissing(id);
            return;
          }
          setMissing(null);
          if (await onChange(action, item, reason.trim())) setReasons({ ...reasons, [id]: '' });
        };
        return (
          <div key={id} className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 space-y-2" data-testid="allowlist-item">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm text-slate-900 dark:text-slate-100">{item.entry_point}</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {item.distribution} {item.distribution_version} · {item.value}
              </span>
              <Badge variant={item.state === 'allowed' ? 'success' : 'warning'} size="sm">
                {item.state === 'allowed' ? 'Allowed' : 'Not allowed'}
              </Badge>
            </div>
            {item.operators.length ? (
              <p className="text-xs text-slate-600 dark:text-slate-400">Operators: {item.operators.join(', ')}</p>
            ) : null}
            <label className="block text-xs text-slate-600 dark:text-slate-400" htmlFor={`reason-${id}`}>
              Reason (required, kept in the history)
            </label>
            <input
              id={`reason-${id}`}
              className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-900 dark:text-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-400"
              value={reason}
              onChange={(e) => setReasons({ ...reasons, [id]: e.target.value })}
            />
            {missing === id ? <p role="alert" className="text-xs text-red-500">Write a reason before changing the allowlist.</p> : null}
            <div className="flex gap-2">
              {item.state === 'allowed' ? (
                <Button size="sm" variant="danger" onClick={() => void act('revoke')}>
                  Revoke {item.entry_point}
                </Button>
              ) : (
                <Button size="sm" onClick={() => void act('allow')}>
                  Allow {item.entry_point}
                </Button>
              )}
            </div>
            {item.history.length ? (
              <ul className="text-xs text-slate-500 dark:text-slate-400 space-y-0.5">
                {item.history.map((h) => (
                  <li key={`${h.created_at}-${h.action}`}>
                    {new Date(h.created_at).toLocaleString()} · {h.action === 'allow' ? 'Allowed' : 'Revoked'} by {h.changed_by}: {h.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

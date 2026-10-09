// Version detail slot: the shortcut audit (FR-004.43). Discovered by feature 002's glob.
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import type { VersionSlot } from '@/components/versions/versionSlots';
import { useCurationStore } from '@/stores/curationStore';
import type { Version } from '@/types/versions';

import { ShortcutAuditCard } from './ShortcutAuditCard';

export function ShortcutAuditSlot({ version }: { version: Version }) {
  const view = useCurationStore((s) => s.auditsByVersion[version.id]);
  const running = useCurationStore((s) => s.running.audit ?? false);
  const error = useCurationStore((s) => s.errors.audit ?? null);
  const fetchAudit = useCurationStore((s) => s.fetchAudit);
  const runAudit = useCurationStore((s) => s.runAudit);
  const [label, setLabel] = useState('');

  useEffect(() => {
    void fetchAudit(version.id);
  }, [version.id, fetchAudit]);

  if (view === undefined && !error) return <p className="text-sm text-slate-500 dark:text-slate-400">Loading the shortcut audit…</p>;
  if (view) return <ShortcutAuditCard version={version} view={view} />;
  return (
    <div className="text-sm">
      <p className="text-slate-500 dark:text-slate-400">
        No shortcut audit yet. It runs by itself after a labeling step; run it now with a categorical label column, for example a source label.
      </p>
      <div className="flex gap-2 mt-2 items-end">
        <label className="text-xs">
          <span className="block text-slate-500 dark:text-slate-400">Label column (optional)</span>
          <input
            aria-label="Label column"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm font-mono focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
        </label>
        <Button size="sm" loading={running} onClick={() => void runAudit(version.id, label.trim() || undefined)}>
          Run the shortcut audit
        </Button>
      </div>
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300 mt-1">{error}</p>}
    </div>
  );
}

const slot: VersionSlot = { id: 'shortcut-audit', order: 100, title: 'Shortcut audit', applies: () => true, Component: ShortcutAuditSlot };
export default slot;

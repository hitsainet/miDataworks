// The audit card (FR-004.32–004.35, 004.42; US-1): a stat tile and an amber callout per warning, the
// column table, the level in force, and "Build <column>-balanced version" for a flagged column.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import type { AuditView } from '@/types/curation';
import type { Version } from '@/types/versions';

import { CellBalancerDialog } from './CellBalancerDialog';
import { ShortcutColumnTable } from './ShortcutColumnTable';
import { ShortcutLevelControl } from './ShortcutLevelControl';
import { pct, pretty } from './format';

export function ShortcutAuditCard({ version, view }: { version: Version; view: AuditView }) {
  const [balancing, setBalancing] = useState<string | null>(null);
  const top = [...view.warnings].sort((a, b) => b.figure - a.figure)[0];
  return (
    <div className="space-y-3">
      {top && (
        <div className="inline-block rounded border border-amber-300 dark:border-amber-700 px-3 py-2" data-testid="shortcut-tile">
          <div className="text-xs text-slate-500 dark:text-slate-400">{pretty(top.column)} predicts label</div>
          <div className="text-xl font-semibold text-amber-700 dark:text-amber-300">{pct(top.figure)}</div>
        </div>
      )}
      {view.warnings.map((w) => (
        <div key={w.column} role="alert" className="rounded border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/30 p-2 text-sm">
          <p>{w.message}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            Warns at {w.level} points (from {w.level_source.replace('_', ' ')}). A warning refuses a public push and a detector-set send.
          </p>
          <Button size="sm" className="mt-2" onClick={() => setBalancing(w.column)}>
            Build {w.column}-balanced version
          </Button>
        </div>
      ))}
      {view.warnings.length === 0 && (
        <p className="text-sm text-green-700 dark:text-green-300">
          No audited column predicts the label {view.level.margin_pp} points above chance and the control ({pct(view.audit.chance, 0)} chance, on{' '}
          {view.audit.n_rows.toLocaleString('en-US')} rows).
        </p>
      )}
      {balancing && (
        <CellBalancerDialog versionId={version.id} datasetId={version.dataset_id} column={balancing} audit={view.audit} onClose={() => setBalancing(null)} />
      )}
      <ShortcutColumnTable audit={view.audit} warnings={view.warnings} />
      <ShortcutLevelControl datasetId={version.dataset_id} />
    </div>
  );
}

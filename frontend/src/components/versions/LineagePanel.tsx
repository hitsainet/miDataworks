// Lineage (FR-002.42): inputs, parent, children, recipe, seed, row-key scheme, digests and reuse.
import type { Lineage } from '@/types/versions';

import { IdentifierChip } from './IdentifierChip';

export function LineagePanel({ lineage, onOpenVersion }: { lineage: Lineage; onOpenVersion: (id: string) => void }) {
  return (
    <section className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5" aria-labelledby="lineage-title">
      <h2 id="lineage-title" className="font-semibold mb-3">Lineage</h2>
      <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-xs">
        <dt className="text-slate-500 dark:text-slate-400">Inputs</dt>
        <dd className="space-y-1">
          {lineage.inputs.map((input, i) => (
            <div key={i}>
              {input.kind === 'source' ? (
                <span>source {String(input.display_name ?? '')} <IdentifierChip value={String(input.revision ?? input.content_hash ?? '')} label="pin" /></span>
              ) : (
                <button type="button" className="text-indigo-700 dark:text-indigo-300 underline focus-visible:ring-2 focus-visible:ring-indigo-500 rounded" onClick={() => onOpenVersion(String(input.version_id))}>
                  version {String(input.number ?? '')}
                </button>
              )}
            </div>
          ))}
        </dd>
        <dt className="text-slate-500 dark:text-slate-400">Children</dt>
        <dd>
          {lineage.children.length === 0 ? '—' : lineage.children.map((c) => (
            <button key={c.version_id} type="button" className="mr-2 text-indigo-700 dark:text-indigo-300 underline rounded focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => onOpenVersion(c.version_id)}>
              v{c.number}{c.state === 'deleted' ? ' (deleted)' : ''}
            </button>
          ))}
        </dd>
        <dt className="text-slate-500 dark:text-slate-400">Recipe</dt>
        <dd><IdentifierChip value={lineage.recipe_hash} label="hash" /></dd>
        <dt className="text-slate-500 dark:text-slate-400">Seed</dt>
        <dd><IdentifierChip value={lineage.seed} /></dd>
        <dt className="text-slate-500 dark:text-slate-400">Row-key scheme</dt>
        <dd className="font-mono">{lineage.rowkey_scheme}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Held-out origin</dt>
        <dd>{lineage.held_out_origin_version_id ? <IdentifierChip value={lineage.held_out_origin_version_id} /> : 'no held-out split'}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Splits</dt>
        <dd className="space-y-1">
          {lineage.splits.map((s) => (
            <div key={s.name} className="flex flex-wrap gap-2">
              <span className="font-medium">{s.name}{s.held_out ? ' (held out)' : ''}</span>
              <span className="tabular-nums">{s.rows.toLocaleString('en-US')} rows</span>
              <IdentifierChip value={s.logical_digest} label="logical" />
              <IdentifierChip value={s.file_sha256} label="file" />
            </div>
          ))}
        </dd>
        <dt className="text-slate-500 dark:text-slate-400">Steps</dt>
        <dd className="space-y-1">
          {lineage.steps.map((s) => (
            <div key={s.index} className="flex flex-wrap gap-2">
              <span className="font-mono">{s.index}</span>
              <span>{s.kind === 'assemble' ? 'assemble' : `${s.operator} ${s.operator_version}`}</span>
              <span className="text-slate-500 dark:text-slate-400">{s.reused ? 'reused' : 'computed'}</span>
            </div>
          ))}
        </dd>
      </dl>
    </section>
  );
}

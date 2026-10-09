// The cell balancer's preview and build (FR-004.37–004.42; US-2, US-3): cells before and after, the
// cap, rows kept, extreme-value flags with "Exclude <value>", and the re-audit of every column
// BEFORE anything is built. Building creates a new version whose parent is this one (R-03.6).
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useCurationStore } from '@/stores/curationStore';
import type { EvaluatedWarning, ShortcutAudit } from '@/types/curation';

import { ShortcutColumnTable } from './ShortcutColumnTable';
import { count, pretty } from './format';

interface Props {
  versionId: string;
  datasetId: string;
  column: string;
  audit: ShortcutAudit;
  onClose: () => void;
}

export function CellBalancerDialog({ versionId, datasetId, column, audit, onClose }: Props) {
  const preview = useCurationStore((s) => s.balancer);
  const running = useCurationStore((s) => s.running.balancer ?? false);
  const error = useCurationStore((s) => s.errors.balancer ?? null);
  const previewCellBalancer = useCurationStore((s) => s.previewCellBalancer);
  const buildCellBalancedVersion = useCurationStore((s) => s.buildCellBalancedVersion);
  const [exclude, setExclude] = useState<string[]>([]);
  const [built, setBuilt] = useState<string | null>(null);
  const derived = audit.excluded_columns.filter((e) => e.reason === 'label_derived').map((e) => e.column);

  useEffect(() => {
    void previewCellBalancer(versionId, column, exclude, audit.label_column, derived);
    // derived is recomputed from the audit, which does not change while the dialog is open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId, column, exclude, audit.label_column, previewCellBalancer]);

  const report = preview?.report;
  const reaudit = report && 'columns' in report.reaudit ? report.reaudit : null;
  const noWarnings: EvaluatedWarning[] = [];

  return (
    <div role="dialog" aria-label={`Build ${column}-balanced version`} className="rounded border border-slate-300 dark:border-slate-600 p-3 mt-3 bg-white dark:bg-slate-900">
      <h3 className="font-semibold text-sm mb-2">Cap every {pretty(column)} × label cell at the smallest</h3>
      {running && <p className="text-xs text-slate-500 dark:text-slate-400">Previewing on a sample of up to 2,000 rows…</p>}
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300">{error}</p>}
      {report && (
        <>
          <p className="text-xs mb-2">
            Cap {count(report.cap)} per cell; {count(report.rows_kept)} of {count(report.rows_in)} sampled rows kept (an estimate from the
            preview sample).
          </p>
          <div className="overflow-x-auto">
            <table className="text-xs min-w-[24rem]">
              <thead>
                <tr className="text-left text-slate-500 dark:text-slate-400">
                  <th className="pr-3">Value</th>
                  <th className="pr-3">Label</th>
                  <th className="pr-3">Rows before</th>
                  <th>Rows after</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(report.cells).map((c) => (
                  <tr key={`${c.value}|${c.label}`}>
                    <td className="pr-3 font-mono">{c.value}</td>
                    <td className="pr-3">{c.label}</td>
                    <td className="pr-3">{count(c.before)}</td>
                    <td>{count(c.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.extreme_values.length > 0 && (
            <ul className="text-xs mt-2 space-y-1">
              {report.extreme_values.map((f) => (
                <li key={`${f.column}=${f.value}`} className="text-amber-700 dark:text-amber-300">
                  {pretty(f.column)} = {f.value}: {count(f.dominant_rows)} of {count(f.rows)} rows are {f.dominant_label}. A value this one-sided is
                  a shortcut of its own.{' '}
                  {f.column === column ? (
                    <Button size="sm" variant="secondary" onClick={() => setExclude([...exclude, f.value])}>
                      Exclude {f.value}
                    </Button>
                  ) : (
                    <span>Add a metadata value filter on {f.column} before this step to exclude it.</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {reaudit && (
            <div className="mt-3">
              <h4 className="text-xs font-semibold mb-1">Every column after balancing</h4>
              <ShortcutColumnTable audit={reaudit} warnings={noWarnings} />
            </div>
          )}
        </>
      )}
      <div className="flex gap-2 justify-end mt-3">
        <Button size="sm" variant="secondary" onClick={onClose}>
          Close the preview
        </Button>
        <Button
          size="sm"
          disabled={!report || running}
          onClick={async () => setBuilt(await buildCellBalancedVersion(versionId, datasetId, column, exclude, audit.label_column, derived))}
        >
          Build {column}-balanced version
        </Button>
      </div>
      {built && <p className="text-xs text-green-700 dark:text-green-300 mt-2">Build started ({built}); the new version appears when it completes.</p>}
    </div>
  );
}

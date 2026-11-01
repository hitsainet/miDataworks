// The role table (mockup `DetectorSets()`): role, version, rows, positive / negative, miStudio view.
import type { ChecksResult, DetectorSetRole } from '@/types/detectorSets';

import { MISTUDIO_VIEW, ROLE_WORDS, count } from './format';

export function RoleTable({ roles, checks }: { roles: DetectorSetRole[]; checks: ChecksResult | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular-nums" data-testid="role-table">
        <thead>
          <tr className="text-xs text-slate-500 dark:text-slate-400">
            <th className="pb-2 text-left font-medium">Role</th>
            <th className="pb-2 text-left font-medium">Version</th>
            <th className="pb-2 text-right font-medium">Rows</th>
            <th className="pb-2 text-right font-medium">Pos / neg</th>
            <th className="pb-2 pl-4 text-left font-medium">miStudio view</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((r) => {
            const values = checks?.label_values[r.id];
            const expected = checks?.expected_counts[r.id];
            const rows = values ? Object.values(values).reduce((a, b) => a + b, 0) : null;
            return (
              <tr key={r.id} className="border-t border-slate-200 dark:border-slate-700/60">
                <td className="py-2">{ROLE_WORDS[r.role]}</td>
                <td className="font-mono text-xs text-slate-500 dark:text-slate-400">
                  {r.dataset_name ?? r.version_id} v{r.version_number ?? '?'} · {r.split}
                </td>
                <td className="text-right">{count(rows)}</td>
                <td className="text-right">
                  {expected ? `${expected.positive ? count(expected.positive) : '—'} / ${count(expected.negative)}` : '—'}
                </td>
                <td className="pl-4 text-xs text-emerald-700 dark:text-emerald-400">{MISTUDIO_VIEW[r.role]}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

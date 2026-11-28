// 021 FR-021.28: the version's sensitivity, with its basis in words, and its weight-space
// programming (WSP) partitions. Shown for every version (sensitivity applies to all); the table only
// when a split carries a mark.
import type { SensitivityBasis, Version } from '@/types/versions';

import type { VersionSlot } from './versionSlots';

const BADGE: Record<string, { words: string; className: string }> = {
  sensitive: {
    words: 'Sensitive (private only)',
    className: 'bg-red-100 text-red-800 dark:bg-red-500/10 dark:text-red-200',
  },
  not_sensitive: {
    words: 'Not sensitive',
    className: 'bg-green-100 text-green-800 dark:bg-green-500/10 dark:text-green-200',
  },
  unknown: {
    words: 'Not declared',
    className: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200',
  },
};

const ROLE_WORDS: Record<string, string> = {
  wsp_practice: 'Practice (may be fitted on)',
  wsp_heldout: 'Held out (evaluation only)',
};

export function basisWords(
  value: string | null | undefined,
  basis: SensitivityBasis | null | undefined,
): string {
  const raised = basis?.raised_by ?? [];
  if (raised.length > 0) {
    return `Raised to sensitive: ${raised.length} source${raised.length === 1 ? '' : 's'} in its lineage ${
      raised.length === 1 ? 'is' : 'are'
    } recorded user traffic.`;
  }
  if (basis?.declared) {
    return `Declared ${basis.declared === 'sensitive' ? 'sensitive' : 'not sensitive'} by the recipe.`;
  }
  if (value === 'sensitive') return 'Inherited from a sensitive input.';
  if (value === 'not_sensitive') return 'Inherited: every input is declared not sensitive.';
  return 'Nothing declares it, so miWorkbench and miForge treat the data as sensitive.';
}

function WspSlot({ version }: { version: Version }) {
  const value = version.sensitivity ?? null;
  const badge = BADGE[value ?? 'unknown'];
  const marked = version.splits.filter((s) => s.wsp);
  const below = version.sensitivity_published_below ?? [];
  return (
    <div className="stack-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span
          data-testid="sensitivity-badge"
          className={`rounded-control px-2 py-0.5 text-xs font-medium ${badge.className}`}
        >
          {badge.words}
        </span>
        <span className="text-slate-600 dark:text-slate-300">
          {basisWords(value, version.sensitivity_basis)}
        </span>
      </div>
      {below.length > 0 && (
        <p className="text-amber-700 dark:text-amber-300">
          {below.length} earlier publish{below.length === 1 ? '' : 'es'} stated a lower value. Read
          this page, not a stored manifest, before the data reaches an outside model.
        </p>
      )}
      {marked.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500 dark:text-slate-400">
            <tr>
              <th className="py-1 pr-2">Split</th>
              <th className="py-1 pr-2">Role</th>
              <th className="py-1 pr-2">Set kind</th>
              <th className="py-1 pr-2">Rows</th>
              <th className="py-1 pr-2">Text column</th>
              <th className="py-1 pr-2">Characters (min / median / max)</th>
              <th className="py-1">Floor</th>
            </tr>
          </thead>
          <tbody>
            {marked.map((s) => (
              <tr key={s.name} className="border-t border-slate-200 dark:border-slate-700">
                <td className="py-1 pr-2 font-mono">{s.name}</td>
                <td className="py-1 pr-2">{ROLE_WORDS[s.wsp!.role] ?? s.wsp!.role}</td>
                <td className="py-1 pr-2">{s.wsp!.set_kind.replace(/_/g, ' ')}</td>
                <td className="py-1 pr-2">{s.rows.toLocaleString()}</td>
                <td className="py-1 pr-2 font-mono">{s.wsp!.text_column}</td>
                <td className="py-1 pr-2">
                  {s.wsp!.version_length.min} / {s.wsp!.version_length.median} /{' '}
                  {s.wsp!.version_length.max}
                </td>
                <td className="py-1">{s.wsp!.min_chars}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const slot: VersionSlot = {
  id: 'wsp',
  order: 460,
  title: 'Sensitivity and weight-space programming (WSP) partitions',
  applies: () => true,
  Component: WspSlot,
};

export default slot;

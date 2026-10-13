// Map each ACTUAL label value (from the checks route) to positive / negative / excluded (FR-009.5).
import type { MappingTarget } from '@/types/detectorSets';

const select =
  'rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function LabelMappingEditor({
  values,
  mapping,
  onChange,
  calibration,
}: {
  values: Record<string, number>;
  mapping: Record<string, MappingTarget>;
  onChange: (next: Record<string, MappingTarget>) => void;
  calibration?: boolean;
}) {
  const keys = Object.keys(values).sort();
  if (keys.length === 0) {
    return <p className="text-xs text-slate-500 dark:text-slate-400">Run the checks to list this split's label values.</p>;
  }
  return (
    <table className="text-sm" data-testid="label-mapping">
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <td className="py-1 pr-3 font-mono text-xs">{k}</td>
            <td className="py-1 pr-3 text-xs tabular-nums text-slate-500">{values[k].toLocaleString('en-US')} rows</td>
            <td>
              <select
                aria-label={`Mapping for ${k}`}
                className={select}
                value={mapping[k] ?? ''}
                onChange={(e) => onChange({ ...mapping, [k]: e.target.value as MappingTarget })}
              >
                <option value="">Not mapped</option>
                {!calibration && <option value="positive">positive</option>}
                <option value="negative">negative</option>
                <option value="excluded">excluded</option>
              </select>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

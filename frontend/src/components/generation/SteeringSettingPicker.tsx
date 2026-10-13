// One steering setting (FR-007.15): unsteered, a miLLM profile by name, or an inline one-feature set.
// Profiles are read by the backend; this picker never calls miLLM.
import type { SteeringSetting } from '@/types/generation';

export function SteeringSettingPicker({ label, value, onChange }: { label: string; value: SteeringSetting; onChange: (s: SteeringSetting) => void }) {
  const id = label.replace(/\s+/g, '-').toLowerCase();
  const inline = value.kind === 'inline' ? value : null;
  return (
    <fieldset className="space-y-2 rounded-lg border border-slate-200 dark:border-slate-700 p-3" data-testid={`setting-${id}`}>
      <legend className="px-1 text-sm font-medium">{label}</legend>
      <label className="block text-xs text-slate-500 dark:text-slate-400" htmlFor={`${id}-kind`}>Steering</label>
      <select
        id={`${id}-kind`}
        aria-label={`${label} steering`}
        className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500"
        value={value.kind}
        onChange={(e) => {
          const kind = e.target.value;
          if (kind === 'profile') onChange({ kind: 'profile', profile_name: '' });
          else if (kind === 'inline') onChange({ kind: 'inline', sae_id: '', features: [{ index: 0, strength: 0 }] });
          else onChange({ kind: 'none' });
        }}
      >
        <option value="none">Unsteered</option>
        <option value="profile">miLLM profile</option>
        <option value="inline">Inline feature set</option>
      </select>
      {value.kind === 'profile' && (
        <input
          aria-label={`${label} profile name`}
          placeholder="Profile name in miLLM"
          className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm text-cyan-700 dark:text-cyan-300"
          value={value.profile_name}
          onChange={(e) => onChange({ kind: 'profile', profile_name: e.target.value })}
        />
      )}
      {inline && (
        <div className="grid grid-cols-3 gap-2">
          <input aria-label={`${label} SAE`} placeholder="SAE id" className="col-span-3 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm" value={inline.sae_id} onChange={(e) => onChange({ ...inline, sae_id: e.target.value })} />
          <input aria-label={`${label} feature index`} type="number" min={0} className="rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm" value={inline.features[0]?.index ?? 0} onChange={(e) => onChange({ ...inline, features: [{ index: Number(e.target.value), strength: inline.features[0]?.strength ?? 0 }] })} />
          <input aria-label={`${label} strength`} type="number" step="0.1" className="col-span-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm" value={inline.features[0]?.strength ?? 0} onChange={(e) => onChange({ ...inline, features: [{ index: inline.features[0]?.index ?? 0, strength: Number(e.target.value) }] })} />
        </div>
      )}
    </fieldset>
  );
}

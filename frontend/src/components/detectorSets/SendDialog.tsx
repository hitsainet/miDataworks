// Send to miStudio: repositories default to <namespace>/<dataset>-v<n>, visibility private
// (FR-009.18, FR-009.78). Disabled while a check refuses, naming that check's next step.
import { useState } from 'react';

import type { CheckOutcome, DetectorSetRole } from '@/types/detectorSets';

const input =
  'rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function defaultRepos(roles: DetectorSetRole[], namespace: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of roles) {
    if (!out[r.version_id] && namespace) out[r.version_id] = `${namespace}/${r.dataset_name ?? 'dataset'}-v${r.version_number ?? 0}`;
  }
  return out;
}

export function SendDialog({
  roles,
  refusal,
  checked,
  unavailable = null,
  onSend,
}: {
  roles: DetectorSetRole[];
  refusal: CheckOutcome | null;
  checked: boolean;
  /** Why no send is possible at all (miStudio not configured), shown instead of the form's own reasons. */
  unavailable?: string | null;
  onSend: (body: { namespace: string; visibility: 'private' | 'public' }) => void;
}) {
  const [namespace, setNamespace] = useState('');
  const [visibility, setVisibility] = useState<'private' | 'public'>('private');
  const repos = defaultRepos(roles, namespace);
  const blocked = unavailable !== null || !checked || refusal !== null || !namespace;
  const why = unavailable
    ? unavailable
    : !checked
    ? 'Run the checks first.'
    : refusal
      ? `${refusal.code}: ${refusal.next_step ?? refusal.reason}`
      : !namespace
        ? 'Name the Hugging Face namespace to publish to.'
        : null;
  return (
    <div className="space-y-2" data-testid="send-dialog">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-slate-600 dark:text-slate-300">
          Hugging Face namespace
          <input aria-label="Hugging Face namespace" className={`${input} block`} value={namespace} onChange={(e) => setNamespace(e.target.value.trim())} />
        </label>
        <label className="text-xs text-slate-600 dark:text-slate-300">
          Visibility
          <select aria-label="Visibility" className={`${input} block`} value={visibility} onChange={(e) => setVisibility(e.target.value as 'private' | 'public')}>
            <option value="private">Private</option>
            <option value="public">Public</option>
          </select>
        </label>
        <button
          type="button"
          disabled={blocked}
          className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
          onClick={() => onSend({ namespace, visibility })}
        >
          Send to miStudio
        </button>
      </div>
      {why && <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="send-blocked">{why}</p>}
      {Object.keys(repos).length > 0 && (
        <ul className="text-xs font-mono text-slate-500 dark:text-slate-400" data-testid="send-repos">
          {Object.values(repos).map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

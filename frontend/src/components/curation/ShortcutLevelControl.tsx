// The warning level (FR-004.33): effective margin, where it came from, history, and a change that
// needs a reason. Agents are refused by the API (403 -> "Only the operator can change this level.").
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useCurationStore } from '@/stores/curationStore';

const SOURCE: Record<string, string> = {
  dataset: 'this dataset’s override',
  global: 'the global level',
  set: 'the global level',
  code_default: 'the default (10 points, P-19)',
};

export function ShortcutLevelControl({ datasetId }: { datasetId: string | null }) {
  const level = useCurationStore((s) => (datasetId ? s.levelsByDataset[datasetId] : null));
  const globalLevel = useCurationStore((s) => s.globalLevel);
  const error = useCurationStore((s) => s.errors.level ?? null);
  const fetchLevel = useCurationStore((s) => s.fetchLevel);
  const fetchGlobalLevel = useCurationStore((s) => s.fetchGlobalLevel);
  const setDatasetLevel = useCurationStore((s) => s.setDatasetLevel);
  const clearDatasetLevel = useCurationStore((s) => s.clearDatasetLevel);
  const setGlobalLevel = useCurationStore((s) => s.setGlobalLevel);
  const [margin, setMargin] = useState('');
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (datasetId) void fetchLevel(datasetId);
    else void fetchGlobalLevel();
  }, [datasetId, fetchLevel, fetchGlobalLevel]);

  const effective = datasetId ? level?.effective_margin_pp : globalLevel?.margin_pp;
  const source = datasetId ? level?.source : globalLevel?.source;
  const history = (datasetId ? level?.history : globalLevel?.history) ?? [];
  const field = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';

  const save = async () => {
    const value = Number(margin);
    const ok = datasetId ? await setDatasetLevel(datasetId, value, reason) : await setGlobalLevel(value, reason);
    if (ok) {
      setMargin('');
      setReason('');
    }
  };

  return (
    <section aria-label={datasetId ? 'Warning level for this dataset' : 'Global warning level'} className="text-sm">
      <p>
        Warning level: <strong>{effective ?? '…'} points</strong> above chance and the permuted-label control, from{' '}
        {source ? SOURCE[source] ?? source : '…'}.
      </p>
      <div className="flex flex-wrap items-end gap-2 mt-2">
        <label className="text-xs">
          <span className="block text-slate-500 dark:text-slate-400">New level (points)</span>
          <input aria-label="New level in points" value={margin} onChange={(e) => setMargin(e.target.value)} inputMode="decimal" className={`${field} w-24`} />
        </label>
        <label className="text-xs grow">
          <span className="block text-slate-500 dark:text-slate-400">Reason (required)</span>
          <input aria-label="Reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} className={`${field} w-full`} />
        </label>
        <Button size="sm" onClick={() => void save()} disabled={!reason.trim() || margin === ''}>
          Save the level
        </Button>
        {datasetId && level?.source === 'dataset' && (
          <Button size="sm" variant="secondary" disabled={!reason.trim()} onClick={() => void clearDatasetLevel(datasetId, reason)}>
            Clear the override
          </Button>
        )}
      </div>
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300 mt-1">{error}</p>}
      {history.length > 0 && (
        <ul className="text-xs text-slate-500 dark:text-slate-400 mt-2 space-y-0.5">
          {history.slice(0, 5).map((h, i) => (
            <li key={i}>
              {h.action === 'set' ? `Set to ${h.margin_pp} points` : 'Override cleared'} by {h.set_by}
              {h.created_at ? ` on ${h.created_at.slice(0, 10)}` : ''}: {h.reason}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

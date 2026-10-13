// Version detail slot: leakage across splits (FR-004.20, 004.43). Exact, near and group crossings,
// counted per split pair, with the first pairs and their excerpts.
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import type { VersionSlot } from '@/components/versions/versionSlots';
import { useCurationStore } from '@/stores/curationStore';
import type { Version } from '@/types/versions';

// A stable empty list: a selector returning a fresh [] would re-render forever (zustand 5).
const NO_PAIRS: never[] = [];

export function LeakageSlot({ version }: { version: Version }) {
  const leakage = useCurationStore((s) => s.leakageByVersion[version.id]);
  const pairs = useCurationStore((s) => s.pairsByVersion[version.id]) ?? NO_PAIRS;
  const running = useCurationStore((s) => s.running.leakage ?? false);
  const error = useCurationStore((s) => s.errors.leakage ?? null);
  const fetchLeakage = useCurationStore((s) => s.fetchLeakage);
  const runLeakage = useCurationStore((s) => s.runLeakage);
  const [group, setGroup] = useState('');
  useEffect(() => {
    void fetchLeakage(version.id);
  }, [version.id, fetchLeakage]);
  const total = leakage
    ? [leakage.exact_pairs, leakage.near_pairs, leakage.group_pairs].reduce((n, b) => n + Object.values(b).reduce((a, x) => a + x, 0), 0)
    : 0;
  return (
    <div className="text-sm">
      {leakage === undefined && !error && <p className="text-slate-500 dark:text-slate-400">Loading the leakage check…</p>}
      {leakage && (
        <div>
          <p className={total ? 'text-amber-700 dark:text-amber-300' : 'text-green-700 dark:text-green-300'}>
            {total
              ? `${total} pair(s) cross between splits (${leakage.n_rows.toLocaleString('en-US')} rows, near-duplicate basis ${leakage.basis}, threshold ${leakage.threshold}).`
              : `No row crosses between splits (${leakage.n_rows.toLocaleString('en-US')} rows checked).`}
          </p>
          <ul className="text-xs mt-1">
            {(['exact_pairs', 'near_pairs', 'group_pairs'] as const).map((k) =>
              Object.entries(leakage[k]).map(([pair, n]) => (
                <li key={`${k}${pair}`}>
                  {k.replace('_pairs', '')}: {pair.replace('|', ' ↔ ')} — {n}
                </li>
              )),
            )}
          </ul>
          {pairs.slice(0, 5).map((p, i) => (
            <p key={i} className="text-xs font-mono mt-1 break-words">
              [{p.kind}] {p.excerpt_a} ↔ {p.excerpt_b}
            </p>
          ))}
        </div>
      )}
      <div className="flex gap-2 items-end mt-2">
        <label className="text-xs">
          <span className="block text-slate-500 dark:text-slate-400">Group column (optional)</span>
          <input
            aria-label="Group column"
            value={group}
            onChange={(e) => setGroup(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm font-mono"
          />
        </label>
        <Button size="sm" variant="secondary" loading={running} onClick={() => void runLeakage(version.id, group.trim() || undefined)}>
          Check leakage across splits
        </Button>
      </div>
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-300 mt-1">{error}</p>}
    </div>
  );
}

const slot: VersionSlot = { id: 'leakage', order: 130, title: 'Leakage across splits', applies: (v) => v.splits.length > 1, Component: LeakageSlot };
export default slot;

// The operators a recipe step can name, read from feature 003's registry (novice walk 2026-10-19,
// finding 7: the editor asked for the operator's name and version typed from memory). One read of
// the unfiltered catalogue per page load, shared by every step row, the guided Curate step and the
// recipe editor; the Operators screen's own filters never narrow it.
import { useEffect, useState } from 'react';

import { operatorsApi } from '@/api/operators';
import type { OperatorEntry } from '@/types/operators';

export interface OperatorChoice {
  name: string;
  /** Every registered version, newest first. */
  versions: OperatorEntry[];
  /** The newest version that runs (allowed), or null when none is allowed. */
  current: OperatorEntry | null;
}

let cached: Promise<OperatorEntry[]> | null = null;

/** Test seam: forget the cached catalogue. */
export function resetOperatorChoices(): void {
  cached = null;
}

function compareVersions(a: string, b: string): number {
  const pa = a.split(/[.-]/).map((p) => (Number.isFinite(Number(p)) ? Number(p) : p));
  const pb = b.split(/[.-]/).map((p) => (Number.isFinite(Number(p)) ? Number(p) : p));
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x === y) continue;
    if (typeof x === 'number' && typeof y === 'number') return x - y;
    return String(x).localeCompare(String(y));
  }
  return 0;
}

export function groupChoices(entries: OperatorEntry[]): OperatorChoice[] {
  const byName = new Map<string, OperatorEntry[]>();
  for (const e of entries) byName.set(e.name, [...(byName.get(e.name) ?? []), e]);
  return [...byName.entries()]
    .map(([name, versions]) => {
      const sorted = [...versions].sort((a, b) => compareVersions(b.version, a.version));
      return { name, versions: sorted, current: sorted.find((v) => v.state === 'allowed') ?? null };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

export type ChoicesState = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; choices: OperatorChoice[] };

export function useOperatorChoices(): ChoicesState {
  const [state, setState] = useState<ChoicesState>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    cached = cached ?? operatorsApi.list().then((list) => list.items);
    cached
      .then((items) => live && setState({ status: 'ready', choices: groupChoices(items) }))
      .catch((e: unknown) => {
        cached = null; // a failed read is retried on the next mount
        if (live) setState({ status: 'error', message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      live = false;
    };
  }, []);
  return state;
}

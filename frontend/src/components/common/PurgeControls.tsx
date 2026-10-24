// Purge (operator decision 2026-10-20): select archived items and delete them permanently. Shared
// by every "Show archived" view (datasets, label runs, generation runs, earlier agent requests).
//
// - Only archived items get a checkbox. When the view shows archived items, the backend's plan is
//   asked for every one of them, so a blocked item shows its blocker inline and cannot be
//   selected.
// - The bar holds the header checkbox ("Select all" / "Deselect all", indeterminate when partly
//   selected), the live count and "Delete permanently".
// - The confirmation lists every selected item: what goes with it (counts and bytes) or, when the
//   fresh plan says it is now blocked, why it is skipped. Only the purgeable ones are sent; the
//   backend purges each on its own and answers per item.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { purgeKey, usePurgeStore } from '@/stores/purgeStore';
import type { PurgeItem, PurgeKind, PurgeOutcome, PurgeParent } from '@/types/purge';
import { formatBytes } from '@/utils/format';

import { Button } from './Button';
import { Modal } from './Modal';

export interface PurgeTarget {
  id: string;
  name: string;
  archived: boolean;
}

export interface PurgeSelection {
  kind: PurgeKind;
  /** The archived targets, in list order. */
  archived: PurgeTarget[];
  /** Archived and not blocked by the last plan. */
  selectable: PurgeTarget[];
  selected: ReadonlySet<string>;
  toggle: (id: string) => void;
  setAll: (on: boolean) => void;
  clear: () => void;
  /** The last plan's refusal for this item, or null. */
  blockerOf: (id: string) => string | null;
  isArchived: (id: string) => boolean;
  refreshPlans: () => Promise<void>;
}

/** One view's selection. `enabled`: the view is showing archived items. */
export function usePurgeSelection(
  kind: PurgeKind,
  targets: PurgeTarget[],
  enabled: boolean,
): PurgeSelection {
  const plans = usePurgeStore((s) => s.plans);
  const planPurge = usePurgeStore((s) => s.planPurge);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const archived = useMemo(
    () => (enabled ? targets.filter((t) => t.archived) : []),
    [enabled, targets],
  );
  const archivedKey = archived.map((t) => t.id).join(',');

  const refreshPlans = useCallback(async () => {
    const ids = archivedKey ? archivedKey.split(',') : [];
    await planPurge(ids.map((id) => ({ kind, id })));
  }, [archivedKey, kind, planPurge]);

  useEffect(() => {
    if (enabled && archivedKey) void refreshPlans();
  }, [enabled, archivedKey, refreshPlans]);

  const blockerOf = useCallback(
    (id: string) => {
      const plan = plans[purgeKey({ kind, id })];
      if (!plan || plan.status !== 'blocked') return null;
      // The blockers themselves read best inline; the message repeats the item's name.
      return plan.blockers.length > 0
        ? plan.blockers.map((b) => b.message).join(' ')
        : (plan.message ?? 'Cannot be deleted.');
    },
    [plans, kind],
  );
  const selectable = useMemo(
    () => archived.filter((t) => blockerOf(t.id) === null),
    [archived, blockerOf],
  );
  const selectableKey = selectable.map((t) => t.id).join(',');

  // Whatever stopped being selectable (unarchived, purged, now blocked, toggle off) is not
  // selected, whatever the raw state still holds.
  const effective = useMemo(() => {
    const allowed = new Set(selectableKey ? selectableKey.split(',') : []);
    return new Set([...selected].filter((id) => allowed.has(id)));
  }, [selected, selectableKey]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const setAll = useCallback(
    (on: boolean) =>
      setSelected(on ? new Set(selectableKey ? selectableKey.split(',') : []) : new Set()),
    [selectableKey],
  );
  const clear = useCallback(() => setSelected(new Set()), []);
  const archivedIds = useMemo(() => new Set(archived.map((t) => t.id)), [archived]);
  const isArchived = useCallback((id: string) => archivedIds.has(id), [archivedIds]);

  return {
    kind,
    archived,
    selectable,
    selected: effective,
    toggle,
    setAll,
    clear,
    blockerOf,
    isArchived,
    refreshPlans,
  };
}

/** The per-item checkbox and, for a blocked item, its blocker. Renders nothing for a shown item. */
export function PurgeItemCheckbox({
  selection,
  id,
  name,
}: {
  selection: PurgeSelection;
  id: string;
  name: string;
}) {
  if (!selection.isArchived(id)) return null;
  const blocker = selection.blockerOf(id);
  return (
    <div className="flex flex-col items-start gap-1 pt-1" data-testid="purge-item">
      <input
        type="checkbox"
        className="rounded border-slate-300 dark:border-slate-600 disabled:opacity-40"
        checked={selection.selected.has(id)}
        disabled={blocker !== null}
        onChange={() => selection.toggle(id)}
        aria-label={
          blocker ? `${name} cannot be deleted permanently` : `Select ${name} for permanent delete`
        }
        title={blocker ?? undefined}
        data-testid="purge-checkbox"
      />
      {blocker && <span className="sr-only">{blocker}</span>}
    </div>
  );
}

/** The blocker under a blocked archived item. */
export function PurgeBlockerNote({ selection, id }: { selection: PurgeSelection; id: string }) {
  if (!selection.isArchived(id)) return null;
  const blocker = selection.blockerOf(id);
  if (!blocker) return null;
  return (
    <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="purge-blocker">
      Cannot be deleted permanently: {blocker}
    </p>
  );
}

const NOUN: Record<PurgeKind, [string, string]> = {
  dataset: ['dataset', 'datasets'],
  label_run: ['label run', 'label runs'],
  generation_run: ['generation run', 'generation runs'],
  approval: ['request', 'requests'],
  rule_set: ['rule set', 'rule sets'],
};

/** Select all, the count and "Delete permanently". Shown only while archived items are listed. */
export function PurgeBar({
  selection,
  onPurged,
}: {
  selection: PurgeSelection;
  onPurged: (purged: string[]) => void | Promise<void>;
}) {
  const box = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const total = selection.selectable.length;
  const count = selection.selected.size;
  const all = total > 0 && count === total;
  const some = count > 0 && !all;
  useEffect(() => {
    if (box.current) box.current.indeterminate = some;
  }, [some]);
  if (selection.archived.length === 0) return null;
  const [one, many] = NOUN[selection.kind];
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 px-3 py-2 text-xs"
      data-testid="purge-bar"
    >
      <label className="inline-flex items-center gap-2 cursor-pointer select-none text-slate-700 dark:text-slate-200">
        <input
          ref={box}
          type="checkbox"
          className="rounded border-slate-300 dark:border-slate-600"
          checked={all}
          disabled={total === 0}
          onChange={() => selection.setAll(!all)}
          data-testid="purge-select-all"
          aria-checked={some ? 'mixed' : all}
        />
        {all ? 'Deselect all' : 'Select all'} archived {many}
      </label>
      <span
        className="text-slate-500 dark:text-slate-400"
        data-testid="purge-count"
        aria-live="polite"
      >
        {count} selected
        {total < selection.archived.length
          ? ` · ${selection.archived.length - total} cannot be deleted`
          : ''}
      </span>
      <Button
        size="sm"
        variant="danger"
        className="ml-auto"
        disabled={count === 0}
        onClick={() => setOpen(true)}
        title={
          count === 0
            ? `Select archived ${many} first.`
            : `Delete the ${count} selected ${count === 1 ? one : many} and everything that belongs only to them.`
        }
        data-testid="purge-open"
      >
        Delete permanently
      </Button>
      {open && (
        <PurgeDialog selection={selection} onClose={() => setOpen(false)} onPurged={onPurged} />
      )}
    </div>
  );
}

const COUNT_LABEL: Record<string, [string, string]> = {
  datasets: ['dataset', 'datasets'],
  versions: ['version', 'versions'],
  builds: ['build', 'builds'],
  reports: ['report', 'reports'],
  diversity_reports: ['diversity report', 'diversity reports'],
  label_runs: ['label run', 'label runs'],
  labels: ['label', 'labels'],
  generation_runs: ['generation run', 'generation runs'],
  generation_records: ['generated record', 'generated records'],
  generation_pairs: ['pair', 'pairs'],
  drafts: ['draft', 'drafts'],
  approvals: ['agent request', 'agent requests'],
  recipes: ['recipe', 'recipes'],
  step_executions: ['step output', 'step outputs'],
  row_events: ['row event', 'row events'],
  jobs: ['job record', 'job records'],
};

/** What the results list says of one item the backend answered for. */
export function describeResult(r: PurgeOutcome): string {
  if (r.status !== 'purged') return `not deleted. ${r.message ?? ''}`;
  if (r.removed_with)
    return `deleted with ${describeParent(r.removed_with)}, whose record counts it`;
  return `deleted (${describeCounts(r.counts)}; ${formatBytes(r.bytes)})`;
}

function describeParent(parent: PurgeParent): string {
  return `${NOUN[parent.kind][0]} ${parent.name ?? parent.id}`;
}

/** "Deleted 24 of 25 (15 with the item they belonged to); freed 3.0 MB." */
export function describeResults(results: PurgeOutcome[]): string {
  const purged = results.filter((r) => r.status === 'purged');
  const withParent = purged.filter((r) => r.removed_with).length;
  const bytes = purged.reduce((n, r) => n + r.bytes, 0);
  const along = withParent > 0 ? ` (${withParent} with the item they belonged to)` : '';
  return `Deleted ${purged.length} of ${results.length}${along}; freed ${formatBytes(bytes)}.`;
}

/** "2 versions, 3 labels, 1 recipe": the counts the operator cares about first, then the rest. */
export function describeCounts(counts: Record<string, number>): string {
  const keys = Object.keys(counts).sort((a, b) => {
    const order = Object.keys(COUNT_LABEL);
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return keys
    .map((k) => {
      const n = counts[k];
      const [one, many] = COUNT_LABEL[k] ?? [k.replace(/_/g, ' '), k.replace(/_/g, ' ')];
      return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
    })
    .join(', ');
}

function PurgeDialog({
  selection,
  onClose,
  onPurged,
}: {
  selection: PurgeSelection;
  onClose: () => void;
  onPurged: (purged: string[]) => void | Promise<void>;
}) {
  const planPurge = usePurgeStore((s) => s.planPurge);
  const purgeItems = usePurgeStore((s) => s.purgeItems);
  const error = usePurgeStore((s) => s.error);
  const [plans, setPlans] = useState<PurgeOutcome[] | null>(null);
  const [results, setResults] = useState<PurgeOutcome[] | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  // The order the operator sees, fixed when the dialog opens.
  const [items] = useState<PurgeItem[]>(() =>
    selection.archived
      .filter((t) => selection.selected.has(t.id))
      .map((t) => ({ kind: selection.kind, id: t.id })),
  );
  const names = useMemo(
    () => Object.fromEntries(selection.archived.map((t) => [t.id, t.name])),
    [selection.archived],
  );

  useEffect(() => {
    let live = true;
    void planPurge(items).then((out) => {
      if (live) setPlans(out);
    });
    return () => {
      live = false;
    };
  }, [items, planPurge]);

  const purgeable = (plans ?? []).filter((p) => p.status === 'purgeable');
  const blocked = (plans ?? []).filter((p) => p.status !== 'purgeable');
  const bytes = purgeable.reduce((n, p) => n + p.bytes, 0);
  const [one, many] = NOUN[selection.kind];

  const confirm = async () => {
    setBusy(true);
    const out = await purgeItems(
      purgeable.map((p) => ({ kind: p.kind, id: p.id })),
      reason.trim(),
    );
    setBusy(false);
    if (!out) return;
    setResults(out.items);
    const gone = out.items.filter((o) => o.status === 'purged').map((o) => o.id);
    selection.clear();
    await onPurged(gone);
  };

  const footer = results ? (
    <Button size="sm" onClick={onClose} data-testid="purge-done">
      Close
    </Button>
  ) : (
    <>
      <Button size="sm" variant="secondary" onClick={onClose} disabled={busy}>
        Cancel
      </Button>
      <Button
        size="sm"
        variant="danger"
        onClick={() => void confirm()}
        loading={busy}
        disabled={busy || plans === null || purgeable.length === 0}
        data-testid="purge-confirm"
      >
        Delete {purgeable.length} {purgeable.length === 1 ? one : many} permanently
      </Button>
    </>
  );

  return (
    <Modal
      id="purge"
      isOpen
      onClose={busy ? () => undefined : onClose}
      title="Delete permanently"
      size="xl"
      footer={footer}
    >
      {results ? (
        <div data-testid="purge-results">
          <p className="text-sm mb-3" data-testid="purge-summary">
            {describeResults(results)} A record of each stays in the purge log.
          </p>
          <ul className="stack-y-2 text-sm">
            {results.map((r) => (
              <li key={r.id} data-testid="purge-result" data-status={r.status}>
                <b>{r.name ?? names[r.id] ?? r.id}</b>: {describeResult(r)}
                {r.paths_left.length > 0 && (
                  <span className="text-amber-700 dark:text-amber-400">
                    {' '}
                    {r.paths_left.length} file path(s) could not be removed:{' '}
                    {r.paths_left.join(', ')}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : plans === null ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Checking what goes with each of the {items.length} selected{' '}
          {items.length === 1 ? one : many}…
        </p>
      ) : (
        <div data-testid="purge-plan">
          <p className="text-sm mb-3">
            This cannot be undone. Each item goes with everything that belongs only to it, files on
            disk included; a short record of what was removed, by whom and why stays.
          </p>
          {error && (
            <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">
              {error}
            </p>
          )}
          <ul className="stack-y-2 text-sm mb-3">
            {plans.map((p) => (
              <li key={p.id} data-testid="purge-plan-item" data-status={p.status}>
                <b>{p.name ?? names[p.id] ?? p.id}</b>
                {p.status === 'purgeable' ? (
                  <span className="text-slate-600 dark:text-slate-300">
                    : removes {describeCounts(p.counts) || 'the record'}; {formatBytes(p.bytes)} on
                    disk
                  </span>
                ) : (
                  <span className="text-amber-700 dark:text-amber-400">: skipped. {p.message}</span>
                )}
              </li>
            ))}
          </ul>
          {blocked.length > 0 && (
            <p
              className="text-xs text-slate-500 dark:text-slate-400 mb-3"
              data-testid="purge-skipped"
            >
              {blocked.length} of {plans.length} cannot be deleted and will be skipped.
            </p>
          )}
          <label className="block text-xs text-slate-600 dark:text-slate-300">
            Reason (kept in the record)
            <input
              className="mt-1 w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
              value={reason}
              maxLength={500}
              onChange={(e) => setReason(e.target.value)}
              data-testid="purge-reason"
            />
          </label>
          {purgeable.length > 0 && (
            <p className="mt-3 text-sm font-medium" data-testid="purge-total">
              Frees {formatBytes(bytes)} across {purgeable.length}{' '}
              {purgeable.length === 1 ? one : many}.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}

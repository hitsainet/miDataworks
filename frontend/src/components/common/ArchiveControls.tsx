// Archive (operator decision 2026-10-20): the shared pieces every archivable view uses. Archive
// hides an item from default lists and pickers and deletes nothing, so it needs no confirmation:
// Unarchive puts it back exactly.
import { useState } from 'react';

import { formatEt } from '@/utils/format';
import type { ArchiveFields } from '@/types/archive';

import { Button } from './Button';

export function isArchived(item: ArchiveFields): boolean {
  return Boolean(item.archived_at);
}

/** "Show archived": the list's toggle. Archived items are left out until it is on. */
export function ShowArchivedToggle({ checked, onChange, testId }: { checked: boolean; onChange: (value: boolean) => void; testId?: string }) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300 cursor-pointer select-none">
      <input
        type="checkbox"
        className="rounded-sm border-slate-300 dark:border-slate-600"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        data-testid={testId ?? 'show-archived'}
      />
      Show archived
    </label>
  );
}

/** Archive or Unarchive, named for what it does to this item. */
export function ArchiveButton({
  item,
  noun,
  onArchive,
  onUnarchive,
}: {
  item: ArchiveFields;
  noun: string;
  onArchive: () => Promise<unknown>;
  onUnarchive: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const archived = isArchived(item);
  const act = async () => {
    setBusy(true);
    try {
      await (archived ? onUnarchive() : onArchive());
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button
      size="sm"
      variant="secondary"
      loading={busy}
      disabled={busy}
      onClick={() => void act()}
      aria-label={`${archived ? 'Unarchive' : 'Archive'} ${noun}`}
      title={archived ? `List this ${noun} again.` : `Hide this ${noun} from lists and pickers. Nothing is deleted.`}
    >
      {archived ? 'Unarchive' : 'Archive'}
    </Button>
  );
}

/** "Archived Oct 20, 2026, 3:12 PM ET by Sean · e2e test data", or nothing when shown. */
export function ArchivedNote({ item, className }: { item: ArchiveFields; className?: string }) {
  if (!isArchived(item)) return null;
  const when = formatEt(item.archived_at);
  return (
    <p className={className ?? 'text-xs text-slate-500 dark:text-slate-400'} data-testid="archived-note">
      Archived{when ? ` ${when}` : ''}{item.archived_by ? ` by ${item.archived_by}` : ''}{item.archive_reason ? ` · ${item.archive_reason}` : ''}.
    </p>
  );
}

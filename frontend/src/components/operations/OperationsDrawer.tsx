// The operations drawer: active and failed jobs, and the self-test job that proves the job
// pipeline end to end (Foundation task 5.8).
import { Button } from '@/components/common/Button';
import { CloseButton, useEscapeToClose } from '@/components/common/DetailPopup';
import { useJobRooms } from '@/hooks/useJobRooms';
import { useJobsStore } from '@/stores/jobsStore';

import { ActiveOperations } from './ActiveOperations';
import { FailedOperations } from './FailedOperations';

export function OperationsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  useJobRooms();
  const startSelftest = useJobsStore((s) => s.startSelftest);
  const error = useJobsStore((s) => s.error);
  const loadError = useJobsStore((s) => s.loadError);
  const notice = useJobsStore((s) => s.lastNotice);
  // Escape closes the drawer; so does moving to another screen (App.tsx), so it never stays over
  // the page's own buttons (novice walk 2026-10-19, finding 10). The drawer covers the Show
  // operations button that opened it, so a click on the page beside it closes it (the backdrop):
  // that is "click again to close" here (operator, 2026-10-31).
  useEscapeToClose(open, onClose);
  if (!open) return null;
  return (
    <>
    <div className="fixed inset-0 z-40 bg-black/20" data-testid="operations-backdrop" aria-hidden="true" onClick={onClose} />
    <aside
      role="dialog"
      aria-label="Operations"
      className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 shadow-xl"
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Operations</h2>
        <CloseButton onClose={onClose} what="operations" />
      </div>
      {error && <p className="mb-3 text-xs text-red-700 dark:text-red-400" role="alert">{error}</p>}
      {loadError && <p className="mb-3 text-xs text-amber-700 dark:text-amber-400">Could not load jobs: {loadError}</p>}
      {notice && <p className="mb-3 text-xs text-slate-600 dark:text-slate-400">{notice}</p>}
      <div className="stack-y-6">
        <ActiveOperations />
        <FailedOperations />
        <div>
          <Button variant="secondary" size="sm" onClick={() => void startSelftest(10)}>
            Run a 10-second self-test job
          </Button>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Writes a Parquet file, reads it back and reports progress, so you can see the job pipeline working.</p>
        </div>
      </div>
    </aside>
    </>
  );
}

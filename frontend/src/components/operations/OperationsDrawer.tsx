// The operations drawer: active and failed jobs, and the self-test job that proves the job
// pipeline end to end (Foundation task 5.8).
import { X } from 'lucide-react';

import { Button } from '@/components/common/Button';
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
  if (!open) return null;
  return (
    <aside
      role="dialog"
      aria-label="Operations"
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 shadow-xl"
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Operations</h2>
        <button type="button" onClick={onClose} className="p-1 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close operations">
          <X size={16} />
        </button>
      </div>
      {error && <p className="mb-3 text-xs text-red-700 dark:text-red-400" role="alert">{error}</p>}
      {loadError && <p className="mb-3 text-xs text-amber-700 dark:text-amber-400">Could not load jobs: {loadError}</p>}
      {notice && <p className="mb-3 text-xs text-slate-600 dark:text-slate-400">{notice}</p>}
      <div className="space-y-6">
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
  );
}

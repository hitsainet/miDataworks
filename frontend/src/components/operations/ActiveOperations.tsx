// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/SystemMonitor/ActiveOperationsSection.tsx
// @ c829a2cc. Mode: adapt (docs/REUSE.md). Kept: the two clocks (elapsed for started work, queued
// for work that has not started). Changed: reads dw_jobs through jobsStore; live progress per job
// room; the cancel button names its action; a queued job shows why it waits, and the shell says
// miLLM serves one model at a time (R-03.64, task 7.4).
import { Clock } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { STATUS_CLASSES } from '@/config/brand';
import { useJobsStore } from '@/stores/jobsStore';
import type { Job } from '@/types/api';
import { elapsedLabel } from '@/utils/format';

export const ONE_MODEL_NOTICE =
  'miLLM serves one model at a time. A job that needs a different model waits until the running one finishes.';

function JobRow({ job }: { job: Job }) {
  const cancelJob = useJobsStore((s) => s.cancelJob);
  const running = job.status === 'running' || job.status === 'cancelling';
  return (
    <li className="rounded-lg border border-slate-200 dark:border-slate-700 p-3" data-testid={`active-job-${job.id}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-900 dark:text-slate-100">{job.kind}</div>
          <div className="text-xs font-mono text-slate-500 dark:text-slate-400 truncate">{job.id} · started by {job.started_by}</div>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-full ${running ? STATUS_CLASSES.running : STATUS_CLASSES.neutral}`}>
          {job.status}
        </span>
      </div>
      <div className="mt-2 h-1.5 rounded-full overflow-hidden bg-slate-200 dark:bg-slate-700" role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100} aria-label={`${job.kind} progress`}>
        <div className="h-full bg-indigo-500" style={{ width: `${job.progress}%` }} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
        <span className="inline-flex items-center gap-1 tabular">
          <Clock size={12} aria-hidden="true" /> {elapsedLabel(job)} · {job.progress.toFixed(0)}% of the job
        </span>
        {job.status !== 'cancelling' && (
          <Button variant="secondary" size="sm" onClick={() => void cancelJob(job.id)}>
            Cancel this job
          </Button>
        )}
      </div>
      {job.status === 'queued' && job.queue_reason && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400" data-testid="queue-reason">{job.queue_reason}</p>
      )}
    </li>
  );
}

export function ActiveOperations() {
  const active = useJobsStore((s) => s.active);
  const waitingOnModel = active.some((j) => j.status === 'queued' && j.required_model_id);
  return (
    <section aria-labelledby="active-ops">
      <h2 id="active-ops" className="text-base font-semibold mb-3 text-slate-900 dark:text-slate-100">Active operations</h2>
      {waitingOnModel && (
        <p className="mb-3 text-xs rounded-lg p-3 bg-slate-100 dark:bg-slate-900 text-slate-600 dark:text-slate-400">{ONE_MODEL_NOTICE}</p>
      )}
      {active.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">Nothing is running. Jobs you start appear here with their progress.</p>
      ) : (
        <ul className="space-y-2">{active.map((job) => <JobRow key={job.id} job={job} />)}</ul>
      )}
    </section>
  );
}

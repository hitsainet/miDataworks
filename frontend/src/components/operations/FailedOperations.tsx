// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/SystemMonitor/FailedOperationsSection.tsx
// @ c829a2cc. Mode: adapt (docs/REUSE.md): failed dw_jobs with their recorded reason; the dismiss
// button names its action and hides the job without deleting its record.
import { Button } from '@/components/common/Button';
import { useJobsStore } from '@/stores/jobsStore';

export function FailedOperations() {
  const failed = useJobsStore((s) => s.failed);
  const dismissJob = useJobsStore((s) => s.dismissJob);
  return (
    <section aria-labelledby="failed-ops">
      <h2 id="failed-ops" className="text-base font-semibold mb-3 text-slate-900 dark:text-slate-100">Failed operations</h2>
      {failed.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No failed jobs.</p>
      ) : (
        <ul className="space-y-2">
          {failed.map((job) => (
            <li key={job.id} className="rounded-lg border border-red-500/30 p-3" data-testid={`failed-job-${job.id}`}>
              <div className="text-sm font-medium text-slate-900 dark:text-slate-100">{job.kind}</div>
              <div className="text-xs font-mono text-slate-500 dark:text-slate-400">{job.id}</div>
              <p className="mt-1 text-xs text-red-700 dark:text-red-400">{job.error ?? 'It failed without recording a reason.'}</p>
              <div className="mt-2 flex justify-end">
                <Button variant="secondary" size="sm" onClick={() => void dismissJob(job.id)}>
                  Dismiss this failure
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

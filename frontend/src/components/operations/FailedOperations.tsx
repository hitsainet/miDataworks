// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/SystemMonitor/FailedOperationsSection.tsx
// @ c829a2cc. Mode: adapt (docs/REUSE.md): failed dw_jobs with their recorded reason; the dismiss
// button names its action and hides the job without deleting its record.
//
// Novice walk 2026-10-19, finding 10: twenty-odd old test failures filled the drawer. Failures are
// now newest first; those of the last day are shown, older ones fold into one line that can be
// opened or dismissed together.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { useJobsStore } from '@/stores/jobsStore';
import type { Job } from '@/types/api';
import { formatDuration } from '@/utils/format';

/** Failures newer than this are shown open; older ones are folded. */
export const RECENT_FAILURE_MS = 24 * 3600 * 1000;

export const failedAt = (job: Job): number => Date.parse(job.completed_at ?? job.created_at);

export function splitFailures(jobs: Job[], now = Date.now()): { recent: Job[]; older: Job[] } {
  const sorted = [...jobs].sort((a, b) => failedAt(b) - failedAt(a));
  return {
    recent: sorted.filter((j) => now - failedAt(j) < RECENT_FAILURE_MS),
    older: sorted.filter((j) => now - failedAt(j) >= RECENT_FAILURE_MS),
  };
}

function FailedRow({ job, now }: { job: Job; now: number }) {
  const dismissJob = useJobsStore((s) => s.dismissJob);
  const ago = Math.max(0, (now - failedAt(job)) / 1000);
  return (
    <li className="rounded-lg border border-red-500/30 p-3" data-testid={`failed-job-${job.id}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{job.kind}</span>
        <span className="text-xs text-slate-500 dark:text-slate-400">failed {formatDuration(ago)} ago</span>
      </div>
      <div className="text-xs font-mono text-slate-500 dark:text-slate-400">{job.id}</div>
      <p className="mt-1 text-xs text-red-700 dark:text-red-400">{job.error ?? 'It failed without recording a reason.'}</p>
      <div className="mt-2 flex justify-end">
        <Button variant="secondary" size="sm" onClick={() => void dismissJob(job.id)}>
          Dismiss this failure
        </Button>
      </div>
    </li>
  );
}

export function FailedOperations() {
  const failed = useJobsStore((s) => s.failed);
  const dismissJobs = useJobsStore((s) => s.dismissJobs);
  // Read once when the drawer opens (it mounts on open), so ages do not shift while you read.
  const [now] = useState(() => Date.now());
  const { recent, older } = splitFailures(failed, now);
  return (
    <section aria-labelledby="failed-ops">
      <h2 id="failed-ops" className="text-base font-semibold mb-3 text-slate-900 dark:text-slate-100">Failed operations</h2>
      {failed.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No failed jobs.</p>
      ) : (
        <>
          {recent.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-2">No failures in the last day.</p>
          ) : (
            <ul className="space-y-2 mb-3">{recent.map((job) => <FailedRow key={job.id} job={job} now={now} />)}</ul>
          )}
          {older.length > 0 && (
            <details className="rounded-lg border border-slate-200 dark:border-slate-700 p-3" data-testid="older-failures">
              <summary className="cursor-pointer text-sm text-slate-600 dark:text-slate-300 focus-visible:ring-2 focus-visible:ring-indigo-500">
                {older.length} older failure{older.length === 1 ? '' : 's'} (more than a day old)
              </summary>
              <div className="mt-2 flex justify-end">
                <Button variant="secondary" size="sm" onClick={() => void dismissJobs(older.map((j) => j.id))}>
                  Dismiss all {older.length} older failure{older.length === 1 ? '' : 's'}
                </Button>
              </div>
              <ul className="space-y-2 mt-2">{older.map((job) => <FailedRow key={job.id} job={job} now={now} />)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}

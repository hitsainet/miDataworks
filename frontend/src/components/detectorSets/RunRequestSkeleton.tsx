// The run request in miStudio's ProbeRunCreate shape, from recorded IDs (FR-009.29). miDataworks
// does not start the run; it offers the IDs to copy and a link to miStudio.
import type { RunRequest } from '@/types/detectorSets';

export function RunRequestSkeleton({ request, mistudioUrl }: { request: RunRequest; mistudioUrl: string }) {
  const text = JSON.stringify(request, null, 2);
  return (
    <div data-testid="run-request">
      <pre className="overflow-x-auto rounded-md bg-slate-100 dark:bg-slate-800 p-3 text-xs font-mono">{text}</pre>
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-2 py-1 text-xs" onClick={() => void navigator.clipboard?.writeText(text)}>
          Copy run request
        </button>
        <a className="rounded-md border border-emerald-300 dark:border-emerald-500/40 px-2 py-1 text-xs text-emerald-700 dark:text-emerald-300" href={mistudioUrl} target="_blank" rel="noreferrer">
          Open miStudio
        </a>
      </div>
    </div>
  );
}

// The files a publish will upload (FR-008.20; "Preview files"): names, sizes, rows and hashes.
import type { PublishBuild } from '@/types/publishing';

export function FilePreview({ build }: { build: PublishBuild | null }) {
  if (!build) return null;
  if (build.status !== 'completed' || !build.files) {
    return <div className="text-sm text-slate-500 dark:text-slate-400 mb-4">Building the split files ({build.status})…</div>;
  }
  return (
    <div className="mb-4 overflow-x-auto" data-testid="file-preview">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-500 dark:text-slate-400">
            <th className="py-1 pr-3 font-medium">File</th>
            <th className="py-1 pr-3 font-medium">Rows</th>
            <th className="py-1 pr-3 font-medium">Bytes</th>
            <th className="py-1 font-medium">SHA-256</th>
          </tr>
        </thead>
        <tbody>
          {build.files.map((f) => (
            <tr key={f.path} className="border-t border-slate-200 dark:border-slate-700/60">
              <td className="py-1 pr-3 font-mono text-xs">
                {f.path}
                {f.held_out && <span className="ml-2 text-amber-700 dark:text-amber-300">held out · evaluation only</span>}
              </td>
              <td className="py-1 pr-3">{f.rows.toLocaleString()}</td>
              <td className="py-1 pr-3">{f.bytes.toLocaleString()}</td>
              <td className="py-1 font-mono text-xs text-slate-500 dark:text-slate-400" title={f.sha256}>
                {f.sha256.slice(0, 12)}…
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Per-step progress of a send: publish -> download -> register, with state chips in words.
import type { SendDetail } from '@/types/detectorSets';

const STATE: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  done: 'Done',
  reused: 'Reused',
  failed: 'Failed',
};

export function SendProgress({ send, onResume, onCancel }: { send: SendDetail; onResume: () => void; onCancel: () => void }) {
  return (
    <div data-testid="send-progress">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span>Send {send.id}: <strong data-testid="send-state">{send.state}</strong></span>
        {(send.state === 'failed' || send.state === 'cancelled') && (
          <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-2 py-1 text-xs" onClick={onResume}>Resume</button>
        )}
        {(send.state === 'queued' || send.state === 'running') && (
          <button type="button" className="rounded-md border border-slate-300 dark:border-slate-600 px-2 py-1 text-xs" onClick={onCancel}>Cancel</button>
        )}
      </div>
      {send.error && (
        <div role="alert" className="mb-2 rounded-md border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm">
          {String(send.error.message ?? '')} {send.error.next_step ? `Next: ${String(send.error.next_step)}` : ''}
        </div>
      )}
      <table className="w-full text-xs">
        <tbody>
          {send.steps.map((s) => (
            <tr key={`${s.step}-${s.unit_key}`} className="border-t border-slate-200 dark:border-slate-700/60">
              <td className="py-1 pr-2 capitalize">{s.step}</td>
              <td className="py-1 pr-2 font-mono">{s.repo_id ?? s.unit_key}</td>
              <td className="py-1 pr-2 font-mono text-emerald-700 dark:text-emerald-400">{s.probe_dataset_id ?? s.mistudio_dataset_id ?? ''}</td>
              <td className="py-1 text-right">{STATE[s.state] ?? s.state}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

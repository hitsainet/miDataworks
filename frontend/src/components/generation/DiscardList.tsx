// Discarded and skipped responses with their reason, the steering requested and the steering miLLM
// reported (FR-007.19 – 007.21). A missing header reads "not reported", never "unsteered".
import type { GenerationRecord } from '@/types/generation';

import { REASON_LABEL } from './format';

export function DiscardList({ records }: { records: GenerationRecord[] }) {
  const rows = records.filter((r) => r.outcome !== 'generated');
  if (rows.length === 0) return <p className="text-sm text-slate-500 dark:text-slate-400">Nothing discarded.</p>;
  return (
    <table className="w-full text-xs" data-testid="discard-list">
      <thead>
        <tr className="text-left text-slate-500 dark:text-slate-400">
          <th className="py-1">Record</th>
          <th>Reason</th>
          <th>Requested</th>
          <th>Reported by miLLM</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.stage}-${r.record_index}`} className="border-t border-slate-200 dark:border-slate-700 align-top">
            <td className="py-1 font-mono">{r.record_index}{r.side ? ` (${r.side})` : ''}</td>
            <td>{REASON_LABEL[r.reason_code ?? ''] ?? r.reason_code}{r.check_reasons.length ? ` — ${r.check_reasons.join(', ')}` : ''}</td>
            <td className="font-mono">{r.requested_set_hash ? r.requested_set_hash.slice(0, 15) : 'unsteered'}</td>
            <td className="font-mono text-cyan-600 dark:text-cyan-400">{r.reported_steering ?? 'not reported'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

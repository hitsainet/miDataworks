// Earlier sends of a set, newest first; each keeps the snapshot it used (FR-009.12).
import type { SendSummary } from '@/types/detectorSets';

export function SendHistory({ sends, onOpen }: { sends: SendSummary[]; onOpen: (id: string) => void }) {
  if (sends.length === 0) return <p className="text-xs text-slate-500 dark:text-slate-400">Not sent yet.</p>;
  return (
    <ul className="text-xs" data-testid="send-history">
      {sends.map((s) => (
        <li key={s.id}>
          <button type="button" className="underline" onClick={() => onOpen(s.id)}>{s.id}</button> · {s.state} · {s.started_by} · {s.created_at}
        </li>
      ))}
    </ul>
  );
}

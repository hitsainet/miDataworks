// Hashes, row keys, seeds and revisions: JetBrains Mono 12 px, shortened, the full value on hover,
// and a copy button (FPRD 002 section 4).
import { Copy } from 'lucide-react';

export function IdentifierChip({ value, label, length = 12 }: { value: string | number | null | undefined; label?: string; length?: number }) {
  if (value === null || value === undefined || value === '') return <span className="text-slate-400">—</span>;
  const text = String(value);
  const short = text.length > length ? `${text.slice(0, length)}…` : text;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs text-slate-700 dark:text-slate-300" title={text}>
      {label && <span className="font-sans text-slate-500 dark:text-slate-400">{label}</span>}
      <span>{short}</span>
      <button
        type="button"
        aria-label={`Copy ${label ?? 'identifier'} ${text}`}
        className="p-0.5 rounded text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        onClick={() => void navigator.clipboard?.writeText(text)}
      >
        <Copy className="w-3 h-3" aria-hidden="true" />
      </button>
    </span>
  );
}

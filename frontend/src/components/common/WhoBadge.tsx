// Who did it (FR-010.35): an agent identity is marked with the Bot icon AND the word "agent", so
// the mark never depends on the icon alone; an operator name is plain text.
import { Bot } from 'lucide-react';

export function isAgent(who: string): boolean {
  return who.startsWith('agent:');
}

export function WhoBadge({ who }: { who: string }) {
  if (!isAgent(who)) return <span data-testid="who-operator">{who}</span>;
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-200"
      data-testid="who-agent"
      title={`Requested by the agent identity ${who}`}
    >
      <Bot size={12} aria-hidden="true" />
      <span>agent</span>
      <span className="font-mono">{who.slice('agent:'.length)}</span>
    </span>
  );
}

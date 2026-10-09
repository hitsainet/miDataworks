// Who made a decision (C5; FR-006.42): the operator's Settings name, or the agent identity marked as
// an agent. With one shared MCP token every agent decision carries one identity; this does not
// claim to tell agents apart (P-12).
export function DecidedBy({ who, origin }: { who: string; origin: 'operator' | 'agent' }) {
  return origin === 'agent' ? (
    <span data-testid="decided-by" data-origin="agent" className="inline-flex items-center gap-1 rounded bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 text-[11px]">
      Agent · <span className="font-mono">{who}</span>
    </span>
  ) : (
    <span data-testid="decided-by" data-origin="operator" className="text-[11px] font-medium">{who}</span>
  );
}

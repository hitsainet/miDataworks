// A probe used as a training reward is never an evaluation (FR-009.70).
export function RewardMarkBadge() {
  return (
    <span className="rounded-full bg-orange-100 dark:bg-orange-500/15 px-2 py-0.5 text-xs text-orange-800 dark:text-orange-200" data-testid="reward-badge">
      Training reward — not an evaluation
    </span>
  );
}

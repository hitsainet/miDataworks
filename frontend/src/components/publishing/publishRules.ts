// What the publish button may do (008 FTASKS 11.3; AC-US2; FR-008.12, 008.13; EC-1). Pure, so the
// rule is tested without rendering. The SERVER enforces the same rule in the publish job; this only
// keeps the button honest.
import type { CheckOutcome, CheckRun, Visibility } from '@/types/publishing';

export interface PublishGate {
  label: string;
  effective: Visibility;
  disabled: boolean;
  /** The first blocker, worded as what to do next; null when the button is enabled. */
  blocker: string | null;
}

export function effectiveVisibility(run: CheckRun | null, requested: Visibility): Visibility {
  const repo = run?.results?.find((r) => r.check === 'repository');
  return repo?.evidence.effective_visibility === 'public' ? 'public' : requested;
}

export function firstBlocker(results: CheckOutcome[], visibility: Visibility): CheckOutcome | null {
  return (
    results.find((r) => r.outcome === 'refused') ??
    (visibility === 'public' ? results.find((r) => r.outcome === 'amber') : undefined) ??
    null
  );
}

export function publishGate(run: CheckRun | null, requested: Visibility, busy: boolean): PublishGate {
  const effective = effectiveVisibility(run, requested);
  const label = effective === 'public' ? 'Publish publicly' : 'Publish privately';
  if (busy) return { label, effective, disabled: true, blocker: 'A publish is running.' };
  if (!run || run.status !== 'completed' || !run.results) {
    return { label, effective, disabled: true, blocker: 'Checks are still running for this choice.' };
  }
  const blocking = firstBlocker(run.results, effective);
  if (blocking) {
    return { label, effective, disabled: true, blocker: `${blocking.check}: ${blocking.reason} ${blocking.next_step}` };
  }
  return { label, effective, disabled: false, blocker: null };
}

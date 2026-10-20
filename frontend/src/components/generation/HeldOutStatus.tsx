// Held-out status of the input version (FR-007.35): generation waits until a held-out split exists.
import type { Refusal } from '@/stores/generationStore';
import type { Plan } from '@/types/generation';

export function HeldOutStatus({ plan, refusal }: { plan: Plan | null; refusal: Refusal | null }) {
  if (refusal?.code === 'HELD_OUT_MISSING') {
    return (
      <div role="alert" data-testid="held-out-status" className="rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm">
        No held-out split yet. Add a split step with a held-out split (Curation → split), build the version, then generate from it.
      </div>
    );
  }
  if (refusal?.code === 'HELD_OUT_SEED') {
    return (
      <div role="alert" data-testid="held-out-status" className="rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm">
        A seed split is held out. Seed rows come only from training splits; remove it from the seed splits.
      </div>
    );
  }
  if (!plan) return null;
  return (
    <p data-testid="held-out-status" className="text-sm text-slate-600 dark:text-slate-300">
      Held-out split{plan.held_out.splits.length === 1 ? '' : 's'}: {plan.held_out.splits.join(', ')} — never seeded, never written. {plan.seed_rows_available.toLocaleString()} source rows available as seeds.
    </p>
  );
}

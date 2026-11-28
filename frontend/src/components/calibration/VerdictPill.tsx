// The verdict pill: colour AND words, never colour alone. The words come from verdictText().
import type { VerdictOut } from '@/types/calibration';

import { VERDICT_TONE, verdictText } from './format';

export function VerdictPill({ verdict }: { verdict: VerdictOut }) {
  return (
    <span data-testid="verdict-pill" data-verdict={verdict.verdict} className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium tabular-nums ${VERDICT_TONE[verdict.verdict]}`}>
      {verdictText(verdict)}
    </span>
  );
}

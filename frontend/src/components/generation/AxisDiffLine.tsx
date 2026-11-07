// "Differs on feature 4127 only: 0 vs 6" or the refusal list (P-22). Asks the backend 300 ms after
// the last change.
import { useEffect } from 'react';

import { useGenerationStore } from '@/stores/generationStore';
import type { SteeringSetting } from '@/types/generation';

export const COMPARE_DEBOUNCE_MS = 300;

export function AxisDiffLine({ a, b }: { a: SteeringSetting; b: SteeringSetting }) {
  const result = useGenerationStore((s) => s.compareResult);
  const compareSettings = useGenerationStore((s) => s.compareSettings);
  const key = JSON.stringify([a, b]);
  useEffect(() => {
    const timer = setTimeout(() => void compareSettings(a, b), COMPARE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, compareSettings]);
  if (!result) return null;
  return (
    <p data-testid="axis-diff" role={result.one_axis ? undefined : 'alert'} className={`text-sm ${result.one_axis ? 'text-slate-600 dark:text-slate-300' : 'text-amber-700 dark:text-amber-400'}`}>
      {result.message}
      {!result.one_axis && ' A steered pair must differ on exactly one feature of one SAE.'}
    </p>
  );
}

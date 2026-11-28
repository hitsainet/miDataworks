// "Keeps 47% [44%, 50%] on a sample of 400 rows" (FR-005.19). The warning sentence is the
// FPRD's; a band that keeps nearly nothing or nearly everything is called out by name.
import { pct } from './format';

export const BAND_WARNING =
  'A band that excludes almost everything starves the training set; one that excludes nothing trusts every guess.';

export interface KeepShare {
  share: number;
  lo: number;
  hi: number;
  n: number;
}

export function KeepShareLine({ estimate, actual }: { estimate: KeepShare | null; actual?: number | null }) {
  if (!estimate) return <p className="text-xs text-slate-500 dark:text-slate-400">{BAND_WARNING}</p>;
  const extreme = estimate.share < 0.05 || estimate.share > 0.95;
  return (
    <div data-testid="keep-share">
      <p className="text-sm">
        Keeps <strong>{pct(estimate.share)}</strong> [{pct(estimate.lo)}, {pct(estimate.hi)}] on a sample of {estimate.n.toLocaleString()} rows
        {actual !== undefined && actual !== null && <> · actual {pct(actual, 1)} of the labeled rows</>}
      </p>
      <p className={`text-xs mt-1 ${extreme ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}`}>{BAND_WARNING}</p>
    </div>
  );
}

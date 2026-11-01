// One formatter per figure kind (FTID 006 section 11): three decimals, tabular figures, every number
// with its sample. verdictText is the ONE source of the verdict pill's words (FTID 006 section 6).
import type { CalibrationStatus, GateRule, Verdict, VerdictOut } from '@/types/calibration';

export const fmt3 = (x: number) => x.toFixed(3);
export const fmtInt = (n: number) => n.toLocaleString('en-US');
export const fmtPct = (x: number) => `${(x * 100).toFixed(1)}%`;

export function formatInterval(i: { value: number; ci_low: number; ci_high: number }): string {
  return `${fmt3(i.value)} [${fmt3(i.ci_low)}, ${fmt3(i.ci_high)}]`;
}

const RULE_TEXT: Record<GateRule, (compared: string, threshold: string, met: boolean) => string> = {
  held_out_rater: (c, t, met) => `${met ? 'level with' : 'below'} held-out rater (${c} ${met ? '≥' : '<'} ${t})`,
  operator_target: (c, t, met) => `CI lower bound ${c} ${met ? '≥' : '<'} ${t} (operator target)`,
  default_c3: (c, t, met) => `CI lower bound ${c} ${met ? '≥' : '<'} ${t} (default)`,
};

export function verdictText(v: Pick<VerdictOut, 'verdict' | 'rule' | 'numbers'>): string {
  if (v.verdict === 'invalid') return 'Invalid · a sanity check failed';
  if (v.verdict === 'insufficient') return 'Insufficient · AUROC needs both human classes';
  const compared = typeof v.numbers.compared === 'number' ? fmt3(v.numbers.compared) : '?';
  const t = v.numbers.threshold;
  const threshold = typeof t === 'number' ? (v.rule === 'default_c3' ? t.toFixed(2) : fmt3(t)) : '?';
  const met = v.verdict === 'passes';
  const head = met ? 'Passes' : 'Fails';
  return v.rule ? `${head} · ${RULE_TEXT[v.rule](compared, threshold, met)}` : head;
}

export const VERDICT_TONE: Record<Verdict, string> = {
  passes: 'border-green-300 dark:border-green-500/40 bg-green-50 dark:bg-green-500/10 text-green-800 dark:text-green-300',
  fails: 'border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 text-amber-800 dark:text-amber-300',
  invalid: 'border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 text-red-800 dark:text-red-300',
  insufficient: 'border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-800/60 text-slate-700 dark:text-slate-300',
};

/** The Label-step callout's reading of a status (005 FR-005.50 against 006 FR-006.20). */
export function statusWords(status: CalibrationStatus | undefined): { tone: 'pass' | 'fail' | 'none'; text: string } {
  if (!status || status.status === 'none_recorded' || !status.verdict) {
    return { tone: 'none', text: 'Not calibrated yet: no calibration record exists for this labeler and question.' };
  }
  if (status.verdict === 'passes') return { tone: 'pass', text: 'Calibrated: this labeler and question pass gate 2 against human labels.' };
  if (status.verdict === 'fails') return { tone: 'fail', text: 'Not calibrated: this labeler and question fail gate 2 against human labels.' };
  return { tone: 'fail', text: `Not calibrated: the latest record is ${status.verdict}, which blocks a public push. Open Calibration to see why.` };
}

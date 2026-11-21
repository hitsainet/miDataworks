// Number copy for feature 005: every number names its scale and sample (FPRD 005 section 4.3).
import type { LabelRun, RowCoverage } from '@/types/labeling';
import { formatEt } from '@/utils/format';

export const pct = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(digits)}%`;

export const prob = (x: number | null | undefined) => (x === null || x === undefined ? '—' : `P ${x.toFixed(3)}`);

/** Measured throughput, or null when none was measured: a run records no rate once it stops, and a
 * dash in its place would read as a value. Callers omit the field on null. */
export const rate = (x: number | null | undefined): string | null =>
  x === null || x === undefined || !Number.isFinite(x) ? null : `${x.toFixed(1)} rows/s`;

export const KIND_LABEL: Record<string, string> = {
  classifier: 'Classifier',
  judge: 'Judge',
  rederived: 'Re-derived',
  aggregate: 'Aggregate',
  probe_verdict: 'Probe verdict',
  feature_tag: 'Feature tags',
};

export const STATE_LABEL: Record<string, string> = {
  awaiting_approval: 'Waiting for approval',
  queued: 'Queued',
  running: 'Labeling',
  cancelled: 'Cancelled',
  completed: 'Completed',
  failed: 'Failed',
  rejected: 'Rejected',
};

/** Rows covered beside the distinct inputs labelled: the same two numbers the gate and a link state. */
export function coverageText(rowsTotal: number, c: RowCoverage | null | undefined): string {
  if (!c) return `${rowsTotal.toLocaleString()} rows`;
  if (c.rows === c.row_keys) return `${c.rows.toLocaleString()} rows`;
  return `${c.rows.toLocaleString()} rows, ${c.row_keys.toLocaleString()} distinct inputs (${c.keys_with_copies.toLocaleString()} input(s) appear in ${c.rows_in_copied_keys.toLocaleString()} rows; each is labelled once and the label applies to every copy)`;
}

/** When a run started and finished, US Eastern with the date (operator 2026-10-31: "a date and time
 *  of start and finish on the tiles and details page"). Started is when its first job began work,
 *  not when it was asked for; a run still going says so instead of a finish. */
export function runTimes(run: Pick<LabelRun, 'state' | 'started_at' | 'completed_at'>): { started: string; finished: string } {
  const waiting = run.state === 'awaiting_approval' || run.state === 'queued';
  const started = formatEt(run.started_at) ?? (waiting ? 'not started yet' : 'not recorded');
  const finished = run.state === 'running' ? 'still running' : waiting ? 'not yet' : formatEt(run.completed_at) ?? 'not recorded';
  return { started, finished };
}


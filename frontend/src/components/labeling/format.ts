// Number copy for feature 005: every number names its scale and sample (FPRD 005 section 4.3).
import type { RowCoverage } from '@/types/labeling';

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

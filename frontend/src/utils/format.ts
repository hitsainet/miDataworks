// Number and duration formatting. Every number names its unit (R-03.62).

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** One GB as `formatBytes` counts it (1024³ bytes), so a GB field and an "In force" line agree. */
const GB = 1024 ** 3;

/** A stored byte count shown in GB, to three decimals with trailing zeros dropped. */
export function bytesToGb(bytes: string): string {
  if (bytes.trim() === '') return '';
  const n = Number(bytes);
  return Number.isFinite(n) ? String(Number((n / GB).toFixed(3))) : '';
}

/** A GB entry as the byte count the setting stores; null when it is not a size above 0. */
export function gbToBytes(gb: string): string | null {
  const n = Number(gb);
  if (gb.trim() === '' || !Number.isFinite(n) || n <= 0) return null;
  return String(Math.round(n * GB));
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/** "Elapsed" for started work, "Queued" for work that has not started (miStudio's two clocks). */
export function elapsedLabel(job: { started_at: string | null; created_at: string }): string {
  const since = job.started_at ?? job.created_at;
  const secs = (Date.now() - Date.parse(since)) / 1000;
  return `${job.started_at ? 'Elapsed' : 'Queued'} ${formatDuration(secs)}`;
}

/** A count with its unit, always: "10,914 rows", "1 row". */
export function formatCount(n: number | null | undefined, unit: string): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  const plural = n === 1 ? unit : unit.endsWith('s') ? unit : `${unit}s`;
  return `${n.toLocaleString('en-US')} ${plural}`;
}

/** The short form of a hash or key: the first 12 characters. */
export function shortId(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : '—';
}

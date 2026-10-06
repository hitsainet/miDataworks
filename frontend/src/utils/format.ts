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

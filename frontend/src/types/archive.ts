// Archive (operator decision 2026-10-20): every archivable item reports these. NULL means shown.
// Optional here only so older fixtures type-check; the backend always sends all three.
export interface ArchiveFields {
  archived_at?: string | null;
  archived_by?: string | null;
  archive_reason?: string | null;
}

// A moment for the approvals surfaces (ENH-008 section 2.7): the browser's local time as the text,
// the exact instant in UTC (ISO 8601) as the tooltip, "unknown" for a missing or bad value.
import { formatLocal, isoInstant } from '@/utils/format';

export function LocalTime({ iso, prefix = '' }: { iso: string | null | undefined; prefix?: string }) {
  const instant = isoInstant(iso);
  return (
    <time dateTime={instant} title={instant} data-testid="local-time">
      {prefix}
      {formatLocal(iso)}
    </time>
  );
}

// A pinned commit or a content hash: JetBrains Mono 12 px, shortened to 8 characters, the full value
// on hover and a copy button (FPRD 001 section 4). Built on 002's IdentifierChip so every identifier
// in the app behaves the same.
import { IdentifierChip } from '@/components/versions/IdentifierChip';

export function CommitChip({ value, label }: { value: string | null | undefined; label?: string }) {
  return <IdentifierChip value={value} label={label} length={8} />;
}

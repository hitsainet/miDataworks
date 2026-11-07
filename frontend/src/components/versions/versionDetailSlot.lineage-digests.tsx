// Feature 002's own slot: the verification history line. It also keeps the slot convention exercised
// in production — a glob that discovers nothing would fail open.
import type { Version } from '@/types/versions';

import type { VersionSlot } from './versionSlots';

function Identity({ version }: { version: Version }) {
  return (
    <dl className="grid grid-cols-[9rem_1fr] gap-y-1 text-xs">
      <dt className="text-slate-500 dark:text-slate-400">Request digest</dt>
      <dd className="font-mono break-all">{version.request_digest}</dd>
      <dt className="text-slate-500 dark:text-slate-400">Manifest SHA-256</dt>
      <dd className="font-mono break-all">{version.manifest_sha256}</dd>
      <dt className="text-slate-500 dark:text-slate-400">Built by</dt>
      <dd>{version.created_by} ({version.created_by_origin})</dd>
    </dl>
  );
}

const slot: VersionSlot = {
  id: 'identity',
  order: 900,
  title: 'Identity',
  applies: () => true,
  Component: Identity,
};

export default slot;

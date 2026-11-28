// The completed versions a run may start from (the version list the other forms read).
import { useEffect, useState } from 'react';

import { versionsApi } from '@/api/datasets';
import type { VersionSummary } from '@/types/versions';

export function useVersionChoices(): VersionSummary[] {
  const [items, setItems] = useState<VersionSummary[]>([]);
  useEffect(() => {
    versionsApi
      .list()
      .then((page) => setItems(page.items.filter((v) => v.state === 'completed')))
      .catch(() => setItems([]));
  }, []);
  return items;
}

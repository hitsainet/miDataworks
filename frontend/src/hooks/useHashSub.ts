// A screen's sub-path from the URL hash (#/<panel>/<sub>), kept current as the hash changes: a click
// that navigates, the browser's Back and Forward, a refresh. utils/navigate.ts writes it; this reads
// it. Null while the hash names another screen or no sub-path.
import { useEffect, useState } from 'react';

import type { ActivePanel } from '@/config/panels';
import { hashPath } from '@/utils/navigate';

function subOf(panel: ActivePanel): string | null {
  const { panel: at, sub } = hashPath();
  return at === panel ? sub : null;
}

export function useHashSub(panel: ActivePanel): string | null {
  const [sub, setSub] = useState(() => subOf(panel));
  useEffect(() => {
    const onHash = () => setSub(subOf(panel));
    onHash();
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [panel]);
  return sub;
}

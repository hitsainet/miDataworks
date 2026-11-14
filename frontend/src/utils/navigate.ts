// Move to another registered screen. The shell listens for the hash change (App.tsx), so every
// screen change goes through the one panel registry. A screen may carry one sub-path: the Help
// screen's topic, so a topic can be bookmarked and linked (#/help/<topic>; ENH-004 D2).
import type { ActivePanel } from '@/config/panels';

export function navigate(panel: ActivePanel, sub?: string): void {
  window.location.hash = sub ? `#/${panel}/${encodeURIComponent(sub)}` : `#/${panel}`;
}

/** The screen and its optional sub-path, from a URL hash such as "#/help/label". */
export function hashPath(hash: string = window.location.hash): { panel: string; sub: string | null } {
  const [panel = '', sub] = hash.replace(/^#\/?/, '').split('/', 2);
  return { panel, sub: sub ? decodeURIComponent(sub) : null };
}

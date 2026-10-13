// Move to another registered screen. The shell listens for the hash change (App.tsx), so every
// screen change goes through the one panel registry.
import type { ActivePanel } from '@/config/panels';

export function navigate(panel: ActivePanel): void {
  window.location.hash = `#/${panel}`;
}

// Re-reads server state when the operator comes back to the tab (window focus, or the page
// becoming visible). A Settings tab left open went stale: it kept the classifier role and the
// template list from when it was opened, so a Save sent the old protocol back over a newer one
// (2026-10-18: the role read 'plugin' after the operator saved TypeSafe from a long-open tab).
import { useEffect } from 'react';

export function useRefreshOnReturn(refresh: () => void): void {
  useEffect(() => {
    const onFocus = () => refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refresh]);
}

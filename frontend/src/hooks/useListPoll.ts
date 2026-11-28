// Re-read a page's list while the page is open, so a run started somewhere else (an agent through
// MCP, another tab, the API) appears without a browser refresh. Operator, 2026-11-27: "I still have
// to refresh the miDataworks Generation page to pick up new jobs." The pages read their list once
// when they opened and then re-read only the runs they already held, and no socket event announces
// a new run, so a run created afterwards never reached the screen.
//
// The poll pauses while the tab is hidden and reads once as soon as it is visible again. A read
// still in flight is not doubled by the next tick.
import { useEffect, useRef } from 'react';

export function useListPoll(refresh: () => unknown, intervalMs: number): void {
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  });

  useEffect(() => {
    let inFlight = false;
    const tick = async () => {
      if (inFlight || document.visibilityState === 'hidden') return;
      inFlight = true;
      try {
        await latest.current();
      } finally {
        inFlight = false;
      }
    };
    const timer = setInterval(() => void tick(), intervalMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void tick();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs]);
}

// What is pinned at the top of the window, and how far down it reaches (operator request
// 2026-11-27: "Please pin the top of the Generation page from the Sort box up").
//
// The window is this app's scroller (the shell has no inner scroll container). Under the sticky top
// bar (`h-14`, 3.5rem) sit, when present, the approvals banner's dock and then a screen's own pinned
// block (the Generation page's heading, buttons and list controls). A focused element scrolled into
// view by Tab or Shift+Tab must land below all of them: Chrome scrolls a focused element into the
// scroller's padded viewport, so `html` takes the pinned total as its `scroll-padding-top`
// (index.css). The same approach as miForge's WSP runs list, written for this app (nothing is
// imported across repos).
//
// Each pinned element registers itself through `usePinned()` (a `ref` callback). The registry keeps
// each element's measured height, follows it with a ResizeObserver, and publishes the total on
// <html> as `--shell-pinned-height`; the property is removed when nothing is registered. An element
// may also publish its own height under a name of its own: the banner's dock publishes
// `--approvals-dock-height`, which a screen's block uses as its sticky `top` so it sits below the
// dock and never over it. There is no list of pinned elements to keep in step: an element counts
// because it registered and stops counting when it unmounts.
import { useCallback } from 'react';

/** The total height of every pinned element below the top bar: `html`'s scroll padding. */
export const SHELL_PINNED_HEIGHT_VAR = '--shell-pinned-height';
/** The approvals banner dock's own height: where a screen's pinned block starts. */
export const APPROVALS_DOCK_HEIGHT_VAR = '--approvals-dock-height';

const heights = new Map<Element, number>();

function publishTotal(): void {
  const style = document.documentElement.style;
  if (heights.size === 0) {
    style.removeProperty(SHELL_PINNED_HEIGHT_VAR);
    return;
  }
  let total = 0;
  for (const height of heights.values()) total += height;
  style.setProperty(SHELL_PINNED_HEIGHT_VAR, `${total}px`);
}

/** How an element is tracked: `ownVar` also carries its own height on <html>; `counted: false`
 *  measures an element inside a pinned block (already counted) without adding it to the total. */
export interface PinnedOptions {
  readonly ownVar?: string;
  readonly counted?: boolean;
}

/** Registers `element` as pinned and returns the cleanup that unregisters it. */
export function trackPinned(element: HTMLElement, { ownVar, counted = true }: PinnedOptions = {}): () => void {
  const style = document.documentElement.style;
  const read = (): void => {
    const height = element.getBoundingClientRect().height;
    if (ownVar) style.setProperty(ownVar, `${height}px`);
    if (counted) {
      heights.set(element, height);
      publishTotal();
    }
  };
  read();
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(read);
  observer?.observe(element);
  return () => {
    observer?.disconnect();
    if (ownVar) style.removeProperty(ownVar);
    if (counted) {
      heights.delete(element);
      publishTotal();
    }
  };
}

/** A stable `ref` callback that registers its element as pinned (React 19 runs the cleanup). */
export function usePinned(ownVar?: string, counted = true): (element: HTMLElement | null) => (() => void) | undefined {
  return useCallback(
    (element: HTMLElement | null) => (element === null ? undefined : trackPinned(element, { ownVar, counted })),
    [ownVar, counted],
  );
}

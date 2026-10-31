// One way to open and close every detail popup (operator, 2026-10-31): "clicking [the tile] to be a
// toggle open, then closed ... for ALL pop up detailed modals in miDataworks."
//
// - toggleOpen: what a tile's click selects. The open item again closes it; another item switches.
// - useEscapeToClose: Escape closes the popup opened last, and only that one, so a dialog open over
//   a detail closes first and the detail stays.
// - CloseButton: the visible X, named "Close" for screen readers, saying what it closes on hover.
//
// A new popup uses all three. DetailPopup.test.tsx drives the mechanism, the Modal and the
// operations drawer; each screen's popup is driven in its own test file (generation runTile178,
// labeling005, review006, sources, versions, curation, detectorSets009, operators).
import { X } from 'lucide-react';
import { useEffect, useRef } from 'react';

/** The selection after clicking `clicked` while `current` is open: the same one closes. */
export function toggleOpen<T>(current: T | null | undefined, clicked: T): T | null {
  return current === clicked ? null : clicked;
}

type Entry = { close: () => void };
const open: Entry[] = [];

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  const top = open[open.length - 1];
  if (!top) return;
  event.preventDefault();
  top.close();
}

/** While `isOpen`, Escape calls `onClose` when this popup is the one opened last. */
export function useEscapeToClose(isOpen: boolean, onClose: () => void): void {
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!isOpen) return undefined;
    const entry: Entry = { close: () => latest.current() };
    open.push(entry);
    if (open.length === 1) window.addEventListener('keydown', onKeyDown);
    return () => {
      const at = open.indexOf(entry);
      if (at >= 0) open.splice(at, 1);
      if (open.length === 0) window.removeEventListener('keydown', onKeyDown);
    };
  }, [isOpen]);
}

/** The X that closes a popup: accessible name "Close", the title says what it closes. */
export function CloseButton({ onClose, what, className = '' }: { onClose: () => void; what: string; className?: string }) {
  return (
    <button
      type="button"
      aria-label="Close"
      title={`Close ${what}`}
      data-testid="popup-close"
      onClick={onClose}
      className={`shrink-0 p-1 rounded-control text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-500 ${className}`}
    >
      <X className="w-5 h-5" aria-hidden="true" />
    </button>
  );
}

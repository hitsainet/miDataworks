// useListPoll (operator, 2026-11-27: a run started elsewhere needed a browser refresh to appear):
// the list is re-read on the interval while the page is visible, not while it is hidden, once as
// soon as it is visible again, never twice at once, and not after the page closes.
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useListPoll } from './useListPoll';

const MS = 5000;

function Probe({ refresh }: { refresh: () => unknown }) {
  useListPoll(refresh, MS);
  return null;
}

let visibility: DocumentVisibilityState = 'visible';
const setVisibility = (state: DocumentVisibilityState) => {
  visibility = state;
  document.dispatchEvent(new Event('visibilitychange'));
};

beforeEach(() => {
  vi.useFakeTimers();
  visibility = 'visible';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useListPoll', () => {
  it('re-reads once per interval while the page is visible', async () => {
    const refresh = vi.fn(async () => undefined);
    render(<Probe refresh={refresh} />);
    expect(refresh).not.toHaveBeenCalled(); // the page's own first read is its mount effect
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS * 2);
    });
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it('does not read while the tab is hidden, and reads at once when it is visible again', async () => {
    const refresh = vi.fn(async () => undefined);
    render(<Probe refresh={refresh} />);
    setVisibility('hidden');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS * 4);
    });
    expect(refresh).not.toHaveBeenCalled();
    await act(async () => {
      setVisibility('visible');
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('does not start a read while the last one is still in flight', async () => {
    let finish: () => void = () => undefined;
    const refresh = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    render(<Probe refresh={refresh} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS * 3);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish();
      await vi.advanceTimersByTimeAsync(MS);
    });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('calls the newest refresh it was given, and stops when the page closes', async () => {
    const first = vi.fn(async () => undefined);
    const second = vi.fn(async () => undefined);
    const { rerender, unmount } = render(<Probe refresh={first} />);
    rerender(<Probe refresh={second} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS);
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MS * 3);
      setVisibility('hidden');
      setVisibility('visible');
    });
    expect(second).toHaveBeenCalledTimes(1);
  });
});

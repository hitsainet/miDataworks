// The shared open/close mechanism of every detail popup (operator, 2026-10-31): the toggle, Escape
// (the popup opened last closes first), the X named "Close", and the Modal and the operations
// drawer that use them. Each screen's own popup is driven in its own test file (see the list in
// DetailPopup.tsx's header).
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { OperationsDrawer } from '@/components/operations/OperationsDrawer';
import { mockFetch } from '@/test/fetchMock';

import { CloseButton, toggleOpen, useEscapeToClose } from './DetailPopup';
import { Modal } from './Modal';

afterEach(() => vi.unstubAllGlobals());

const escape = () => fireEvent.keyDown(window, { key: 'Escape' });

describe('toggleOpen', () => {
  it('opens a closed item, closes the open one, and switches to another', () => {
    expect(toggleOpen(null, 'a')).toBe('a');
    expect(toggleOpen('a', 'a')).toBeNull();
    expect(toggleOpen('a', 'b')).toBe('b');
    expect(toggleOpen(undefined, 'a')).toBe('a');
  });
});

function Popup({ name, onClose }: { name: string; onClose: () => void }) {
  useEscapeToClose(true, onClose);
  return <div data-testid={`popup-${name}`}><CloseButton onClose={onClose} what={name} /></div>;
}

describe('useEscapeToClose', () => {
  it('Escape closes only the popup opened last, then the one under it', () => {
    function Two() {
      const [outer, setOuter] = useState(true);
      const [inner, setInner] = useState(true);
      return (
        <>
          {outer && <Popup name="outer" onClose={() => setOuter(false)} />}
          {inner && <Popup name="inner" onClose={() => setInner(false)} />}
        </>
      );
    }
    render(<Two />);
    escape();
    expect(screen.queryByTestId('popup-inner')).toBeNull();
    expect(screen.getByTestId('popup-outer')).toBeInTheDocument();
    escape();
    expect(screen.queryByTestId('popup-outer')).toBeNull();
  });

  it('does nothing while closed, and other keys do nothing', () => {
    const onClose = vi.fn();
    function Closed() {
      useEscapeToClose(false, onClose);
      return null;
    }
    render(<Closed />);
    escape();
    expect(onClose).not.toHaveBeenCalled();
    const open = vi.fn();
    render(<Popup name="p" onClose={open} />);
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(open).not.toHaveBeenCalled();
    escape();
    expect(open).toHaveBeenCalledTimes(1);
  });
});

describe('CloseButton', () => {
  it('is named Close, says what it closes, and closes once per click', () => {
    const onClose = vi.fn();
    render(<CloseButton onClose={onClose} what="this run" />);
    const button = screen.getByRole('button', { name: 'Close' });
    expect(button).toHaveAttribute('title', 'Close this run');
    fireEvent.click(button);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('Modal (every dialog built on it)', () => {
  it('closes by its X, by Escape and by a click on the backdrop', () => {
    const onClose = vi.fn();
    const { container } = render(<Modal id="m" title="Details" isOpen onClose={onClose}>body</Modal>);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    escape();
    fireEvent.click(container.ownerDocument.querySelector('.bg-black\\/60')!);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('a modal over a detail popup closes first on Escape; the detail stays', () => {
    const detail = vi.fn();
    const modal = vi.fn();
    render(<><Popup name="detail" onClose={detail} /><Modal id="m" title="Over it" isOpen onClose={modal}>x</Modal></>);
    escape();
    expect(modal).toHaveBeenCalledTimes(1);
    expect(detail).not.toHaveBeenCalled();
  });
});

describe('the operations drawer', () => {
  function Shell() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen((v) => !v)}>Show operations</button>
        <OperationsDrawer open={open} onClose={() => setOpen(false)} />
      </>
    );
  }
  const drawer = () => screen.queryByRole('dialog', { name: 'Operations' });

  it('opens from its button; its X, Escape and a click beside it (the backdrop over the button) close it', () => {
    mockFetch({ 'GET /api/v1/jobs': { items: [], total: 0, page: 1, limit: 50 } });
    render(<Shell />);
    const show = screen.getByRole('button', { name: 'Show operations' });
    fireEvent.click(show);
    expect(drawer()).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(drawer()).toBeNull();
    fireEvent.click(show);
    escape();
    expect(drawer()).toBeNull();
    fireEvent.click(show);
    fireEvent.click(screen.getByTestId('operations-backdrop'));
    expect(drawer()).toBeNull();
  });
});

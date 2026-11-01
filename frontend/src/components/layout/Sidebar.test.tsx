import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_MANUAL_BASE_URL, MANUAL_BASE_URL } from '@/config/manual';
import { PANELS } from '@/config/panels';
import { APP_VERSION } from '@/config/version';
import { useUIStore } from '@/stores/uiStore';

import { Sidebar } from './Sidebar';

// The suite foot (operator, 2026-10-20): Settings with the Manual book icon on the same row, the
// version below it, exactly as miStudio and miLLM draw it.
const root = resolve(__dirname, '../../..');
const pkgVersion = (
  JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8')) as { version: string }
).version;

function renderSidebar(onPanelChange = vi.fn()) {
  render(<Sidebar activePanel="datasets" onPanelChange={onPanelChange} />);
  return { onPanelChange, foot: screen.getByTestId('sidebar-foot') };
}

describe('Sidebar foot', () => {
  beforeEach(() => {
    act(() => useUIStore.setState({ sidebar: { collapsed: false, mobileOpen: false } }));
  });

  it('holds Settings, the Manual link and the version, in that order, and nothing else', () => {
    const { foot } = renderSidebar();
    const focusable = Array.from(foot.querySelectorAll('button, a')).map((el) =>
      el.getAttribute('data-testid'),
    );
    expect(focusable).toEqual(['nav-settings', 'sidebar-manual']);
    expect(foot.textContent).toBe(`Settingsv${pkgVersion}`);
    // Settings and the book icon share one row; the version is the next row.
    const row = within(foot).getByTestId('nav-settings').parentElement!.parentElement!;
    expect(row).toContainElement(within(foot).getByTestId('sidebar-manual'));
    expect(row).not.toContainElement(within(foot).getByTestId('sidebar-version'));
  });

  it('opens Settings from the foot', () => {
    const { onPanelChange, foot } = renderSidebar();
    fireEvent.click(within(foot).getByRole('button', { name: 'Settings' }));
    expect(onPanelChange).toHaveBeenCalledTimes(1);
    expect(onPanelChange).toHaveBeenCalledWith('settings');
  });

  it('links the book icon to the manual home in a new tab', () => {
    const { foot } = renderSidebar();
    const link = within(foot).getByRole('link', { name: 'User manual (opens in a new tab)' });
    expect(link).toHaveAttribute('href', MANUAL_BASE_URL);
    expect(MANUAL_BASE_URL).toBe(DEFAULT_MANUAL_BASE_URL); // no VITE_MANUAL_BASE_URL in tests
    expect(link).toHaveAttribute('href', 'https://hitsainet.github.io/miDataworks/');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveAttribute('title', 'User manual');
    expect(link.querySelector('svg.lucide-book-open')).not.toBeNull();
  });

  it('shows the version from package.json in small muted monospace', () => {
    const { foot } = renderSidebar();
    const version = within(foot).getByTestId('sidebar-version');
    expect(version).toHaveTextContent(`v${pkgVersion}`);
    expect(version.className).toMatch(/text-\[10px\]/);
    expect(version.className).toMatch(/font-mono/);
    expect(version.className).toMatch(/text-slate-500/);
  });

  it('collapsed, keeps Settings (with a tooltip) and hides the manual link and version, as miStudio does', () => {
    act(() => useUIStore.setState({ sidebar: { collapsed: true, mobileOpen: false } }));
    const { foot } = renderSidebar();
    expect(within(foot).getByTestId('nav-settings')).toHaveAttribute('title', 'Settings');
    expect(within(foot).queryByTestId('sidebar-manual')).toBeNull();
    expect(within(foot).queryByTestId('sidebar-version')).toBeNull();
    expect(screen.getByRole('button', { name: 'Expand the sidebar' })).toBeInTheDocument();
  });

  it('keeps Help reachable as the last main-list item, not in the foot', () => {
    const { foot } = renderSidebar();
    const main = screen.getByRole('navigation', { name: 'Screens' });
    const ids = within(main)
      .getAllByRole('button')
      .map((b) => b.dataset.testid);
    expect(ids[ids.length - 1]).toBe('nav-help');
    expect(within(foot).queryByTestId('nav-help')).toBeNull();
    expect(PANELS.filter((p) => 'foot' in p && p.foot).map((p) => p.id)).toEqual(['settings']);
  });

  it('toggles collapse from the floating edge button', () => {
    renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'Collapse the sidebar' }));
    expect(useUIStore.getState().sidebar.collapsed).toBe(true);
  });
});

// The sidebar at every width, as miStudio and miLLM draw it (operator, 2026-10-31): a narrow pane
// showed a scrolling tab strip instead. jsdom applies no media queries, so the gating is asserted
// on the classes that would apply it.
describe('Sidebar at every width', () => {
  beforeEach(() => {
    act(() => useUIStore.setState({ sidebar: { collapsed: false, mobileOpen: false } }));
  });

  it('carries no class that hides it below a breakpoint', () => {
    act(() => {
      window.innerWidth = 600;
      window.dispatchEvent(new Event('resize'));
    });
    renderSidebar();
    const aside = screen.getByTestId('sidebar');
    const classes = aside.className.split(/\s+/);
    expect(classes).toContain('flex');
    expect(classes).not.toContain('hidden');
    expect(classes.filter((c) => /^(sm|md|lg|xl|2xl):/.test(c))).toEqual([]);
    expect(within(aside).getAllByRole('button').length).toBeGreaterThan(PANELS.length);
  });

  it('collapses to the 64 px icon rail and back, and the choice is remembered', () => {
    renderSidebar();
    const aside = screen.getByTestId('sidebar');
    expect(aside.className).toMatch(/\bw-56\b/);
    fireEvent.click(screen.getByTestId('sidebar-toggle'));
    expect(aside.className).toMatch(/\bw-16\b/);
    expect(aside.className).not.toMatch(/\bw-56\b/);
    expect(screen.getByTestId('nav-datasets')).toHaveAttribute('title', 'Datasets');
    expect(screen.getByTestId('nav-datasets')).not.toHaveTextContent('Datasets');
    const stored = JSON.parse(window.localStorage.getItem('midataworks-ui-preferences') ?? '{}') as { state?: { sidebar?: { collapsed?: boolean } } };
    expect(stored.state?.sidebar?.collapsed).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Expand the sidebar' }));
    expect(aside.className).toMatch(/\bw-56\b/);
  });

  it('the layout module offers no tab strip to replace it', async () => {
    const layout = await import('./Sidebar');
    expect(Object.keys(layout)).toEqual(['Sidebar']);
  });
});

describe('APP_VERSION source of truth', () => {
  it('comes from frontend/package.json via the Vite define, and agrees with the backend', () => {
    expect(APP_VERSION).toBe(pkgVersion);
    const pyproject = readFileSync(resolve(root, '../backend/pyproject.toml'), 'utf8');
    expect(pyproject).toMatch(new RegExp(`^version = "${pkgVersion.replace(/\./g, '\\.')}"$`, 'm'));
  });
});

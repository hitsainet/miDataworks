import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App, { PANEL_STORAGE_KEY, initialPanel } from './App';
import { PANELS } from './config/panels';
import { HEALTH, mockFetch } from './test/fetchMock';
import { useUIStore } from './stores/uiStore';

const BASE = {
  'GET /api/health': HEALTH,
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/settings': [],
  'GET /api/v1/endpoint-roles': [],
};

describe('App shell', () => {
  beforeEach(() => {
    mockFetch(BASE);
    window.location.hash = '';
  });
  afterEach(() => vi.unstubAllGlobals());

  it('reaches every registered screen from the navigation, and only those', async () => {
    render(<App />);
    for (const panel of PANELS) {
      fireEvent.click(screen.getByTestId(`nav-${panel.id}`));
      expect(screen.getByTestId(`screen-${panel.id}`)).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 1, name: panel.title })).toBeInTheDocument();
    }
    const navButtons = screen.getAllByTestId(/^nav-/);
    expect(navButtons.map((b) => b.dataset.testid)).toEqual(PANELS.map((p) => `nav-${p.id}`));
    await waitFor(() => expect(screen.getByTestId('chip-millm')).toHaveTextContent('connected'));
  });

  it('registers all twelve R-03.60 screens, Rule signals (016), Code rules (018) and Help (novice walk 2026-10-19, finding 14)', () => {
    expect(PANELS.map((p) => p.label)).toEqual([
      'Datasets', 'New dataset', 'Version detail', 'Recipes', 'Label runs', 'Calibration',
      'Review', 'Generation', 'Detector sets', 'Rule signals', 'Operators', 'Code rules', 'Publish and export', 'Approvals', 'Help', 'Settings',
    ]);
  });

  it('restores the persisted screen and ignores an unknown one', () => {
    window.localStorage.setItem(PANEL_STORAGE_KEY, 'operators');
    expect(initialPanel()).toBe('operators');
    window.localStorage.setItem(PANEL_STORAGE_KEY, 'no-such-panel');
    expect(initialPanel()).toBe('datasets');
    window.location.hash = '#/publish';
    expect(initialPanel()).toBe('publish');
  });

  it('toggles light and dark mode on the html element', () => {
    act(() => useUIStore.setState({ theme: 'dark' }));
    render(<App />);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    fireEvent.click(screen.getByTestId('theme-toggle'));
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(useUIStore.getState().theme).toBe('light');
  });

  it('draws miLLM in cyan only when connected, and miStudio neutral when not configured', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('chip-millm')).toHaveTextContent('connected'));
    expect(screen.getByTestId('chip-millm').className).toMatch(/cyan/);
    expect(screen.getByTestId('chip-mistudio')).toHaveTextContent('not configured');
    expect(screen.getByTestId('chip-mistudio').className).not.toMatch(/emerald/);
  });

  it('uses the indigo logo tile and indigo active nav item, 224 px sidebar', () => {
    render(<App />);
    expect(screen.getByTestId('logo-tile').className).toMatch(/bg-indigo-500/);
    expect(screen.getByTestId('nav-datasets').className).toMatch(/bg-indigo-500\/10/);
    expect(screen.getByTestId('sidebar').className).toMatch(/w-56/);
  });

  // R-03.58 (000 FTASKS 18.9, 2026-10-21): the parts of the shell no other test held.
  it('names the app and its tagline beside the logo, collapses to 64 px, and draws lucide icons', () => {
    act(() => useUIStore.setState({ sidebar: { collapsed: false, mobileOpen: false } }));
    render(<App />);
    const sidebar = screen.getByTestId('sidebar');
    expect(sidebar).toHaveTextContent('miDataworks');
    expect(sidebar).toHaveTextContent('Dataset Curation & Labeling');
    expect(screen.getByTestId('logo-tile').querySelector('svg.lucide')).not.toBeNull();
    expect(screen.getByTestId('nav-datasets').querySelector('svg.lucide')).not.toBeNull();
    act(() => useUIStore.setState({ sidebar: { collapsed: true, mobileOpen: false } }));
    expect(screen.getByTestId('sidebar').className).toMatch(/\bw-16\b/);
    expect(screen.getByTestId('sidebar')).not.toHaveTextContent('Dataset Curation & Labeling');
    act(() => useUIStore.setState({ sidebar: { collapsed: false, mobileOpen: false } }));
  });

  it("shows the app's own CPU, memory and disk from /api/health in the top bar", async () => {
    render(<App />);
    const topbar = screen.getByTestId('topbar');
    await waitFor(() => expect(topbar).toHaveTextContent('CPU: 12%'));
    expect(topbar).toHaveTextContent('RAM: 1.0 GB / 16.0 GB');
    expect(topbar).toHaveTextContent('Disk: 32.0 GB / 1.0 TB');
  });
});

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App, { PANEL_STORAGE_KEY, initialPanel } from './App';
import { PANEL_COMPONENTS } from './config/panelComponents';
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

  it('registers all twelve R-03.60 screens', () => {
    expect(PANELS.map((p) => p.label)).toEqual([
      'Datasets', 'New dataset', 'Version detail', 'Recipes', 'Label runs', 'Calibration',
      'Review', 'Generation', 'Detector sets', 'Operators', 'Publish and export', 'Settings',
    ]);
  });

  it('names the building feature on an unbuilt screen', () => {
    // every screen is built now, so the empty state is reached by unregistering one
    const built = PANEL_COMPONENTS['detector-sets'];
    delete PANEL_COMPONENTS['detector-sets'];
    try {
      render(<App />);
      fireEvent.click(screen.getByTestId('nav-detector-sets'));
      expect(screen.getByText(/Feature 009 Detector sets and the miStudio loop builds it/)).toBeInTheDocument();
    } finally {
      PANEL_COMPONENTS['detector-sets'] = built;
    }
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
});

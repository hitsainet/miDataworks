// Feature 020 (FR-020.18; FTASKS 15.1, control M43): the header's app chips read miServer and
// miWorkbench. The keys stay: `chip-millm` / `chip-mistudio` test ids, the health block's
// `dependencies.millm` / `dependencies.mistudio`, and the colour keys (PD-10, register DR-36).
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { APP_CHIP_CLASSES } from '@/config/brand';
import { useHealthStore } from '@/stores/healthStore';
import { HEALTH } from '@/test/fetchMock';

import { Header } from './Header';

describe('Header app chips', () => {
  afterEach(() => useHealthStore.setState({ health: null as never }));

  it('chips read miServer and miWorkbench with frozen keys', () => {
    const health = {
      ...HEALTH,
      dependencies: {
        ...HEALTH.dependencies,
        millm: { ok: true, reason: null, configured: true, url: 'http://miserver.test/api/health' },
        mistudio: { ok: true, reason: null, configured: true, url: 'http://miworkbench.test/api/health' },
      },
    };
    useHealthStore.setState({ health: health as never });
    render(<Header onOpenOperations={() => undefined} />);
    const server = screen.getByTestId('chip-millm');
    const workbench = screen.getByTestId('chip-mistudio');
    expect(server).toHaveTextContent('miServer · connected');
    expect(workbench).toHaveTextContent('miWorkbench · connected');
    // Each chip is fed by its own frozen health key and drawn in its own app colour.
    expect(server.className).toContain(APP_CHIP_CLASSES.millm);
    expect(workbench.className).toContain(APP_CHIP_CLASSES.mistudio);
    expect(APP_CHIP_CLASSES.millm).not.toBe(APP_CHIP_CLASSES.mistudio);
    expect(screen.getByTestId('topbar').textContent).not.toMatch(/mi(llm|studio)/i);
  });

  it('each chip follows its own dependency, not the other one', () => {
    const health = {
      ...HEALTH,
      dependencies: {
        ...HEALTH.dependencies,
        millm: { ok: false, reason: 'refused', configured: true },
        mistudio: { ok: true, reason: null, configured: true },
      },
    };
    useHealthStore.setState({ health: health as never });
    render(<Header onOpenOperations={() => undefined} />);
    expect(screen.getByTestId('chip-millm')).toHaveTextContent('miServer · unreachable');
    expect(screen.getByTestId('chip-mistudio')).toHaveTextContent('miWorkbench · connected');
  });
});

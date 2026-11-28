// The pinned-stack registry (operator request 2026-11-27: pin the top of the Generation page) and its
// wiring. jsdom has no layout, so each pinned element is given a height by its test id; an element
// that stops registering drops out of the total and goes red here. The real stickiness, the banner
// offset and the keyboard scroll are proven in a browser: e2e/generation-pinned.spec.ts.
import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApprovalsBanner } from '@/components/approvals/ApprovalsBanner';
import { GENERATION_CONTROLS_HEIGHT_VAR, GENERATION_PANE_CLASS, GenerationPanel } from '@/components/panels/GenerationPanel';
import { getPanel } from '@/config/panels';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { mockFetch } from '@/test/fetchMock';

import { APPROVALS_DOCK_HEIGHT_VAR, SHELL_PINNED_HEIGHT_VAR, trackPinned, usePinned } from './pinned';

const root = () => document.documentElement.style;
const total = () => root().getPropertyValue(SHELL_PINNED_HEIGHT_VAR);

/** Heights by test id; anything else measures 0. */
let heights: Record<string, number> = {};
beforeEach(() => {
  heights = {};
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const height = heights[this.dataset.testid ?? ''] ?? 0;
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: height, width: 0, height, toJSON: () => ({}) } as DOMRect;
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  root().removeProperty(SHELL_PINNED_HEIGHT_VAR);
  root().removeProperty(APPROVALS_DOCK_HEIGHT_VAR);
  root().removeProperty(GENERATION_CONTROLS_HEIGHT_VAR);
});

function element(testId: string, height: number): HTMLElement {
  heights[testId] = height;
  const el = document.createElement('div');
  el.dataset.testid = testId;
  return el;
}

/** A ResizeObserver that the test triggers by hand. */
function stubResizeObserver() {
  const observers: Array<{ callback: () => void; targets: Element[]; live: boolean }> = [];
  class Fake {
    entry: { callback: () => void; targets: Element[]; live: boolean };
    constructor(callback: () => void) {
      this.entry = { callback, targets: [], live: true };
      observers.push(this.entry);
    }
    observe(target: Element) { this.entry.targets.push(target); }
    unobserve() {}
    disconnect() { this.entry.live = false; }
  }
  vi.stubGlobal('ResizeObserver', Fake);
  return {
    observers,
    resize: (target: Element) => observers.filter((o) => o.live && o.targets.includes(target)).forEach((o) => o.callback()),
  };
}

describe('trackPinned: the total of everything pinned, on <html>', () => {
  it('sums every registered element, follows a resize and removes the property when none is left', () => {
    const ro = stubResizeObserver();
    const dock = element('dock', 120);
    const block = element('block', 150);
    const stopDock = trackPinned(dock, { ownVar: APPROVALS_DOCK_HEIGHT_VAR });
    expect(total()).toBe('120px');
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('120px');
    const stopBlock = trackPinned(block);
    expect(total()).toBe('270px');

    heights.dock = 60; // the banner collapsed
    ro.resize(dock);
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('60px');
    expect(total()).toBe('210px');

    stopDock();
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('');
    expect(total()).toBe('150px');
    expect(ro.observers.filter((o) => o.live)).toHaveLength(1);
    stopBlock();
    expect(total()).toBe('');
  });

  it('measures an uncounted element under its own name only', () => {
    const block = element('block', 150);
    const controls = element('controls', 40);
    const stopBlock = trackPinned(block);
    const stopControls = trackPinned(controls, { ownVar: GENERATION_CONTROLS_HEIGHT_VAR, counted: false });
    expect(total()).toBe('150px');
    expect(root().getPropertyValue(GENERATION_CONTROLS_HEIGHT_VAR)).toBe('40px');
    stopControls();
    expect(root().getPropertyValue(GENERATION_CONTROLS_HEIGHT_VAR)).toBe('');
    expect(total()).toBe('150px');
    stopBlock();
  });

  it('works without a ResizeObserver (measured once)', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    const stop = trackPinned(element('block', 90));
    expect(total()).toBe('90px');
    stop();
    expect(total()).toBe('');
  });
});

describe('usePinned: a stable ref callback', () => {
  it('returns the same callback across renders, so React does not re-register the element', () => {
    const { result, rerender } = renderHook(({ name }) => usePinned(name), { initialProps: { name: APPROVALS_DOCK_HEIGHT_VAR } });
    const first = result.current;
    rerender({ name: APPROVALS_DOCK_HEIGHT_VAR });
    expect(result.current).toBe(first);
  });

  it('registers on mount and unregisters on unmount', () => {
    function Probe() {
      const ref = usePinned();
      return <div ref={ref} data-testid="probe" />;
    }
    heights.probe = 77;
    const { unmount } = render(<Probe />);
    expect(total()).toBe('77px');
    unmount();
    expect(total()).toBe('');
  });
});

const RUN = {
  id: 'gr_1', mode: 'standard', target_type: 'sft', state: 'completed', input_version_id: 'v-1', held_out_splits: ['test'],
  prompt_column: 'prompt', seed_splits: ['train'], sample_size: 10, seed: 1, n_responses: 1, stages: ['respond'],
  generation_endpoint: { model_id: 'm' }, server_kind: 'millm', generator_identities: [], judge_identity: null, chosen_side: null,
  engine_path: 'native', pinned: true, revision_reported: true, model_revision: null, failure_reason: null, error: null,
  warnings: [], counts: {}, snapshots: [], started_by: 'Ada', started_by_origin: 'operator', created_at: '2026-11-01T12:00:00Z',
  completed_at: '2026-11-01T12:05:00Z', job_ids: [], current_job_id: null, room: 'dataworks/generation-runs/gr_1', resumable: false,
};

const PENDING = {
  id: 'apr_1', action: 'hub_push', target: 'x', summary: 'Publish', payload: {}, request_digest: 'abc', requested_by: 'agent:dataworks-mcp',
  status: 'pending', expires_at: new Date(Date.now() + 3 * 3600_000).toISOString(), decided_by: null, created_at: new Date().toISOString(),
};

describe('[wiring] the Generation page and the approvals dock count in the pinned stack', () => {
  beforeEach(() => {
    useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, selectedId: null, error: null });
    useApprovalsStore.setState({ pending: [], error: null, errorId: null, loadError: null });
  });

  it('pins every control from the heading down to Sort in one registered block', async () => {
    mockFetch({ 'GET /api/v1/generation-runs': { items: [RUN], total: 1, page: 1, limit: 50 } });
    heights['generation-pinned'] = 150;
    heights['generation-list-controls'] = 40;
    render(<GenerationPanel panel={getPanel('generation')} />);
    const block = await screen.findByTestId('generation-pinned');
    await within(block).findByTestId('sort-runs');
    // Inside the block: the heading, its subtitle, Help and Manual, the three buttons, Show archived,
    // the two filters and Sort. A control moved out of it scrolls away again.
    const inside = within(block);
    inside.getByRole('heading', { level: 1, name: 'Generation' });
    inside.getByText('Synthetic rows and steered pairs generated through an endpoint.');
    inside.getByTestId('help-link-generation');
    inside.getByTestId('manual-link-generation');
    for (const name of ['New standard run', 'New steered-pair run', 'Templates']) inside.getByRole('button', { name });
    for (const id of ['show-archived-generation-runs', 'filter-state', 'filter-behaviour', 'sort-runs']) inside.getByTestId(id);
    // The runs are not in it: they scroll beneath.
    expect(within(block).queryByTestId('generation-run-card')).toBeNull();
    expect(screen.getByTestId('generation-run-card')).toBeInTheDocument();
    // Sticky under the top bar and the dock, opaque in both themes.
    expect(block).toHaveClass('sticky', 'top-[calc(3.5rem_+_var(--approvals-dock-height,0px))]', 'bg-slate-50', 'dark:bg-slate-950');
    // Registered: its height is the pinned total; the controls inside it are measured, not counted twice.
    expect(total()).toBe('150px');
    expect(root().getPropertyValue(GENERATION_CONTROLS_HEIGHT_VAR)).toBe('40px');
  });

  it('stacks the dock and the block, and drops each when it goes', async () => {
    mockFetch({
      'GET /api/v1/generation-runs': { items: [RUN], total: 1, page: 1, limit: 50 },
      'GET /api/v1/approvals': { approvals: [PENDING] },
    });
    heights['generation-pinned'] = 150;
    heights['approvals-banner-dock'] = 120;
    const panel = render(<GenerationPanel panel={getPanel('generation')} />);
    const banner = render(<ApprovalsBanner />);
    await screen.findByTestId('approvals-banner-dock');
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('120px');
    expect(total()).toBe('270px');
    banner.unmount();
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('');
    expect(total()).toBe('150px');
    panel.unmount();
    expect(total()).toBe('');
  });

  it('counts nothing for the banner when no request waits', async () => {
    mockFetch({ 'GET /api/v1/approvals': { approvals: [] } });
    heights['approvals-banner-dock'] = 120;
    render(<ApprovalsBanner />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('approvals-banner-dock')).toBeNull();
    expect(total()).toBe('');
    expect(root().getPropertyValue(APPROVALS_DOCK_HEIGHT_VAR)).toBe('');
  });
});

// The right-hand pane (operator, 2026-11-27: a run opened far down the list showed its detail out of
// view). In two columns it is sticky right below the pinned stack and scrolls inside itself; in one
// column, opening a run or a form scrolls it into view. The browser proof is e2e/generation-pane.spec.ts.
describe('[wiring] the right-hand pane sticks below the pinned stack', () => {
  let revealed: Element[] = [];
  beforeEach(() => {
    useGenerationStore.setState({ list: [], byId: {}, records: {}, pairs: {}, live: {}, selectedId: null, error: null });
    useApprovalsStore.setState({ pending: [], error: null, errorId: null, loadError: null });
    revealed = [];
    // jsdom has no scrollIntoView; record which element asked, and how.
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value(this: HTMLElement, options: ScrollIntoViewOptions) {
        expect(options).toEqual({ block: 'start' });
        revealed.push(this);
      },
    });
  });
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  const routes = {
    'GET /api/v1/generation-runs': { items: [RUN], total: 1, page: 1, limit: 50 },
    'GET /api/v1/generation-runs/gr_1': RUN,
    'GET /api/v1/generation-runs/gr_1/records': { items: [], total: 0, page: 1, limit: 50 },
    'GET /api/v1/generation-runs/gr_1/pairs': { items: [], total: 0, page: 1, limit: 50 },
  };

  it('holds the run detail and every form in one sticky pane, offset by the pinned total', async () => {
    mockFetch(routes);
    render(<GenerationPanel panel={getPanel('generation')} />);
    const pane = await screen.findByTestId('generation-pane');
    expect(pane.className).toBe(GENERATION_PANE_CLASS);
    expect(pane).toHaveClass(
      'lg:sticky',
      'lg:top-[calc(3.5rem_+_var(--shell-pinned-height,0px)_+_0.75rem)]',
      'lg:max-h-[calc(100dvh_-_3.5rem_-_var(--shell-pinned-height,0px)_-_2.5rem)]',
      'lg:overflow-y-auto',
    );
    fireEvent.click(await screen.findByTestId('generation-run-card'));
    await within(pane).findByTestId('generation-run-detail');
    for (const [button, testId] of [['New standard run', 'new-standard-run'], ['New steered-pair run', 'new-steered-run']] as const) {
      fireEvent.click(screen.getByRole('button', { name: button }));
      within(pane).getByTestId(testId);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Templates' }));
    expect(within(pane).queryByTestId('new-steered-run')).toBeNull();
  });

  it('scrolls the pane into view when a run or a form opens out of view, and not when it closes', async () => {
    mockFetch(routes);
    // The pane's top (0 here) is above the pinned block's bottom (150): out of view, as in one column.
    heights['generation-pinned'] = 150;
    render(<GenerationPanel panel={getPanel('generation')} />);
    const pane = await screen.findByTestId('generation-pane');
    expect(revealed).toEqual([]); // nothing on load
    const card = await screen.findByTestId('generation-run-card');
    fireEvent.click(card);
    await screen.findByTestId('generation-run-detail');
    expect(revealed).toEqual([pane]);
    fireEvent.click(card); // the open run's card closes it
    expect(screen.queryByTestId('generation-run-detail')).toBeNull();
    expect(revealed).toEqual([pane]);
    fireEvent.click(screen.getByRole('button', { name: 'New standard run' }));
    expect(revealed).toEqual([pane, pane]);
  });

  it('leaves the window alone when the pane is already in view (two columns, sticky)', async () => {
    mockFetch(routes);
    heights['generation-pinned'] = 0; // the pane's top (0) is at the block's bottom: in view
    render(<GenerationPanel panel={getPanel('generation')} />);
    fireEvent.click(await screen.findByTestId('generation-run-card'));
    await screen.findByTestId('generation-run-detail');
    fireEvent.click(screen.getByRole('button', { name: 'New steered-pair run' }));
    expect(revealed).toEqual([]);
  });
});

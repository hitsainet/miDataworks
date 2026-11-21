// Label runs, operator requests of 2026-10-31: "a date and time of start and finish on the tiles and
// details page", and "progress on the label run panels requires a manual browser refresh to update.
// They should update automatically."
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getPanel } from '@/config/panels';
import { LABEL_RUN_POLL_MS } from '@/hooks/useLabelRun';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { mockFetch } from '@/test/fetchMock';
import { run } from '@/test/fixtures005';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';

import { LabelRunCard } from './LabelRunCard';
import { LabelRunDetail } from './LabelRunDetail';

// 03:11 UTC on Oct 30 is 11:11 PM on Oct 29 in US Eastern (EDT).
const STARTED = '2026-10-30T03:11:00Z';
const FINISHED = '2026-10-30T05:42:00Z';
const LABELS = { items: [], total: 0, page: 1, limit: 500 };

beforeEach(() => useLabelRunsStore.setState({ list: [], byId: {}, selectedId: null, error: null, labels: {}, live: {} }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('start and finish times', () => {
  it('a running tile names when it started, with the date, and says it is still running', () => {
    render(<LabelRunCard run={run({ started_at: STARTED })} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('run-times')).toHaveTextContent('Started Oct 29, 2026, 11:11 PM ET · Finished still running');
  });

  it('a finished tile names both times; started is the job start, not when the run was asked for', () => {
    render(
      <LabelRunCard
        run={run({ state: 'completed', created_at: '2026-10-29T12:00:00Z', started_at: STARTED, completed_at: FINISHED })}
        onOpen={() => undefined}
        selected={false}
      />,
    );
    expect(screen.getByTestId('run-times')).toHaveTextContent('Started Oct 29, 2026, 11:11 PM ET · Finished Oct 30, 2026, 1:42 AM ET');
  });

  it('a queued tile says it has not started yet', () => {
    render(<LabelRunCard run={run({ state: 'queued', started_at: null })} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('run-times')).toHaveTextContent('Started not started yet · Finished not yet');
  });

  it('the open run names both times', () => {
    mockFetch({ 'GET /api/v1/label-runs/lr_1/labels': LABELS });
    render(<LabelRunDetail run={run({ state: 'cancelled', started_at: STARTED, completed_at: FINISHED })} />);
    const times = screen.getByTestId('run-detail-times');
    // a description list: each term names its time
    const pairs = Array.from(times.querySelectorAll('dt')).map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(pairs).toEqual([
      ['Started', 'Oct 29, 2026, 11:11 PM ET'],
      ['Finished', 'Oct 30, 2026, 1:42 AM ET'],
    ]);
  });
});

describe('the list updates while runs go, without a browser refresh', () => {
  it('every unfinished tile re-reads its run until it ends, and only unfinished ones', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let reads = 0;
    const { calls } = mockFetch({
      'GET /api/v1/label-runs': {
        items: [run({ started_at: STARTED }), run({ id: 'lr_done', state: 'completed', started_at: STARTED, completed_at: FINISHED })],
        total: 2,
        page: 1,
        limit: 50,
      },
      'GET /api/v1/label-runs/lr_1': () => {
        reads += 1;
        return {
          json: reads < 2
            ? run({ started_at: STARTED, rows_done: 500 })
            : run({ state: 'completed', started_at: STARTED, completed_at: FINISHED, rows_done: 25000 }),
        };
      },
    });
    render(<LabelRunsPanel panel={getPanel('label-runs')} />);
    await waitFor(() => expect(screen.getAllByTestId('label-run-card')).toHaveLength(2));
    const tile = () => screen.getAllByTestId('label-run-card')[0];
    expect(tile()).toHaveTextContent('6 of 25,000 rows');
    // No run is open: the tile moves on its own.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LABEL_RUN_POLL_MS);
    });
    await waitFor(() => expect(tile()).toHaveTextContent('500 of 25,000 rows'));
    expect(within(tile()).getByTestId('run-times')).toHaveTextContent('Finished still running');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LABEL_RUN_POLL_MS);
    });
    await waitFor(() => expect(tile()).toHaveTextContent('25,000 of 25,000 rows'));
    expect(within(tile()).getByTestId('run-times')).toHaveTextContent('Finished Oct 30, 2026, 1:42 AM ET');
    // Ended: nothing is requested any more (no timer runs with no unfinished run), and the
    // finished run was never re-read.
    const settled = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LABEL_RUN_POLL_MS * 3);
    });
    expect(calls.slice(settled).map((c) => c.path)).toEqual([]);
    const runReads = calls.filter((c) => c.method === 'GET' && /^\/api\/v1\/label-runs\/[^/]+$/.test(c.path));
    expect(runReads.map((c) => c.path)).toEqual(['/api/v1/label-runs/lr_1', '/api/v1/label-runs/lr_1']);
  });

  it('a tile takes a live progress event while the run goes, never after it ends', () => {
    const live = { rows_done: 900, rows_total: 25000, rows_per_second: null, eta_seconds: null, counts: {} };
    const { rerender } = render(<LabelRunCard run={run({ rows_done: 6 })} live={live} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('label-run-card')).toHaveTextContent('900 of 25,000 rows');
    rerender(<LabelRunCard run={run({ rows_done: 1200 })} live={live} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('label-run-card')).toHaveTextContent('1,200 of 25,000 rows');
    rerender(<LabelRunCard run={run({ state: 'completed', rows_done: 25000 })} live={live} onOpen={() => undefined} selected={false} />);
    expect(screen.getByTestId('label-run-card')).toHaveTextContent('25,000 of 25,000 rows');
  });
});

// Version detail parts (tasks 15.3–15.7): tiles, drop log, lineage, row history, slots, compare.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { COMPARE, DROPPED, KEY, META, VERSION } from '@/test/fixtures002';
import { mockFetch } from '@/test/fetchMock';
import type { RowHistory } from '@/types/versions';

import { CompareView } from './CompareView';
import { DropLogCard } from './DropLogCard';
import { IdentifierChip } from './IdentifierChip';
import { RowBrowser } from './RowBrowser';
import { RowHistoryResult } from './RowHistoryDrawer';
import { StatTiles, tilesFor } from './StatTiles';
import { VersionSlotHost } from './VersionSlotHost';
import { collectSlots, discoveredSlots } from './versionSlots';

afterEach(() => vi.unstubAllGlobals());

describe('StatTiles', () => {
  it('measures the version and names every scale', () => {
    const tiles = tilesFor(VERSION, META);
    expect(tiles.map((t) => t.label)).toEqual(['Rows', 'Dropped by the recipe', 'Held out', 'Keyed on']);
    expect(tiles[1].value).toBe('14,086 rows');
    expect(tiles[1].note).toBe('of 25,000 rows in');
    expect(tiles[2].value).toBe('1,090 rows');
    expect(tiles[3].value).toBe('text');
    render(<StatTiles version={VERSION} meta={META} />);
    expect(screen.getByText('10,914')).toBeInTheDocument();
  });
});

describe('DropLogCard', () => {
  it('lists reasons in step order with the running count, kinds in words', () => {
    render(<DropLogCard steps={VERSION.drop_summary} />);
    const steps = screen.getAllByTestId('drop-step');
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveTextContent('25,000 in → 11,805 out');
    expect(steps[0]).toHaveTextContent('13,195');
    expect(steps[0]).toHaveTextContent('reused from an earlier build');
    expect(steps[1]).toHaveTextContent('dropped · balance');
    expect(steps[2]).toHaveTextContent('split_assigned');
  });
});

describe('IdentifierChip', () => {
  it('shortens with the full value on hover and offers a copy button', () => {
    render(<IdentifierChip value={KEY} label="row key" />);
    expect(screen.getByTitle(KEY)).toHaveTextContent(`${KEY.slice(0, 12)}…`);
    expect(screen.getByRole('button', { name: new RegExp(`Copy row key ${KEY}`) })).toBeInTheDocument();
  });
});

describe('row history', () => {
  it('names the step, reason, statistic and origin of a dropped row', () => {
    render(<RowHistoryResult result={DROPPED} />);
    expect(screen.getByTestId('row-status')).toHaveTextContent('Dropped');
    expect(screen.getByText(/Left at version 1, step 1 \(stub_drop_short 1\)/)).toBeInTheDocument();
    expect(screen.getByText('[length = 5]')).toBeInTheDocument();
    expect(screen.getByText(/train:6/)).toBeInTheDocument();
  });

  it('says present, and lists changes', () => {
    const present: RowHistory = { ...DROPPED, status: 'present', dropped_at: null, present_in: [{ version_id: 'v-2', split: 'train', occurrence: 0 }], trail: [{ ...DROPPED.dropped_at!, kind: 'changed', to_key: 'b'.repeat(64) }] };
    render(<RowHistoryResult result={present} />);
    expect(screen.getByTestId('row-status')).toHaveTextContent('Present in this version');
    expect(screen.getByText(/changed · too_short · became bbbbbbbbbbbb/)).toBeInTheDocument();
  });

  it('says not found, naming how much was searched', () => {
    render(<RowHistoryResult result={{ ...DROPPED, status: 'not_found', dropped_at: null, origin: null }} />);
    expect(screen.getByTestId('row-status')).toHaveTextContent('Not found');
    expect(screen.getByText(/Searched 2 version\(s\)/)).toBeInTheDocument();
  });

  it('the browser asks by key or by text and opens the drawer', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/versions/v-2/rows': { items: [{ _dw_row_key: KEY, _dw_occurrence: 0, _dw_split: 'train', text: 'Why did the chicken cross the road?' }], total: 1, page: 1, limit: 50, columns: [] },
      'GET /api/v1/versions/v-2/rows/history': { query: null, results: [DROPPED] },
    });
    render(<RowBrowser version={VERSION} />);
    await waitFor(() => expect(screen.getByText('Why did the chicken cross the road?')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Row key or text'), { target: { value: KEY.slice(0, 16) } });
    fireEvent.click(screen.getByRole('button', { name: 'Why did this row leave?' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Why did this row leave?' })).toBeInTheDocument());
    expect(calls.find((c) => c.path.includes('/rows/history'))?.path).toContain(`row_key=${KEY.slice(0, 16)}`);
    fireEvent.change(screen.getByLabelText('Row key or text'), { target: { value: 'chicken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Why did this row leave?' }));
    await waitFor(() => expect(calls.filter((c) => c.path.includes('q=chicken')).length).toBe(1));
  });
});

describe('Version detail slots', () => {
  it('discovers slot files through the glob (feature 002 ships one itself)', () => {
    expect(discoveredSlots().map((s) => s.id)).toContain('identity');
  });

  it('renders a discovered slot that applies, and skips one that does not', () => {
    const slots = collectSlots({
      a: { default: { id: 'histogram', order: 1, title: 'Judge probability', applies: () => true, Component: () => <p>histogram here</p> } },
      b: { default: { id: 'never', order: 2, title: 'Never', applies: () => false, Component: () => <p>never</p> } },
      c: { notASlot: true },
    });
    render(<VersionSlotHost version={VERSION} slots={slots} />);
    expect(screen.getByTestId('slot-histogram')).toHaveTextContent('histogram here');
    expect(screen.queryByText('never')).not.toBeInTheDocument();
  });
});

describe('CompareView', () => {
  it('captions every chart with both sample sizes', () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    render(<CompareView report={COMPARE} />);
    expect(screen.getByText('Characters in text, v2 on 9 rows, v1 on 10 rows')).toBeInTheDocument();
    expect(screen.getByText('1 key')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Compare v2 with v1' })).toBeInTheDocument();
  });
});

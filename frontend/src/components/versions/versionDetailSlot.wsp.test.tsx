// 021 FTASKS 14.2, 14.5 (FR-021.28, FR-021.29): the version slot is discovered, says the value and
// its basis in words, shows the partitions only when marks exist, reads in light and dark mode;
// the checks panel shows the S-1 note like any other outcome.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CheckCallout } from '@/components/publishing/ChecksPanel';
import { VERSION } from '@/test/fixtures002';
import type { Version } from '@/types/versions';

import slot, { basisWords } from './versionDetailSlot.wsp';
import { discoveredSlots } from './versionSlots';

const MARK = {
  role: 'wsp_heldout' as const,
  set_kind: 'long_untruncated',
  text_column: 'body',
  min_chars: 400,
  version_length: { unit: 'characters' as const, min: 412, max: 2000, median: 900 },
};

function withSplits(extra: Partial<Version>): Version {
  return {
    ...VERSION,
    splits: [
      { ...VERSION.splits[0], name: 'fit', wsp: { ...MARK, role: 'wsp_practice', set_kind: 'in_distribution', min_chars: 0 } },
      { ...VERSION.splits[0], name: 'hold', held_out: true, wsp: MARK },
      { ...VERSION.splits[0], name: 'test', held_out: true, wsp: null },
    ],
    ...extra,
  };
}

describe('versionDetailSlot.wsp', () => {
  it('is discovered by the slot glob, for every version', () => {
    const found = discoveredSlots().find((s) => s.id === 'wsp');
    expect(found).toBeDefined();
    expect(found?.applies(VERSION)).toBe(true);
  });

  it.each([
    ['sensitive', { declared: 'sensitive' as const, raised_by: [] }, 'Sensitive (private only)', 'Declared sensitive by the recipe.'],
    ['not_sensitive', { declared: null, raised_by: [] }, 'Not sensitive', 'Inherited: every input is declared not sensitive.'],
    [null, { declared: null, raised_by: [] }, 'Not declared', 'miWorkbench and miForge treat the data as sensitive'],
    ['sensitive', { declared: null, raised_by: [{ kind: 'session_data_source', source_id: 's1' }] }, 'Sensitive (private only)', '1 source in its lineage is recorded user traffic'],
  ])('says %s in words with its basis', (value, basis, badge, words) => {
    const v = withSplits({ sensitivity: value as Version['sensitivity'], sensitivity_basis: basis });
    render(<slot.Component version={v} />);
    expect(screen.getByTestId('sensitivity-badge')).toHaveTextContent(badge);
    expect(screen.getByText((t) => t.includes(words))).toBeInTheDocument();
  });

  it('lists the marked splits only, with role words and lengths', () => {
    render(<slot.Component version={withSplits({ sensitivity: 'sensitive' })} />);
    const rows = screen.getAllByRole('row');
    expect(rows).toHaveLength(3); // header + fit + hold; test is not a partition
    expect(rows[2]).toHaveTextContent('Held out (evaluation only)');
    expect(rows[2]).toHaveTextContent('412 / 900 / 2000');
    expect(rows[1]).toHaveTextContent('Practice (may be fitted on)');
  });

  it('shows no table when no split is marked, and warns of a publish stated lower', () => {
    render(<slot.Component version={{ ...VERSION, sensitivity: 'sensitive', sensitivity_published_below: ['pub_1'] }} />);
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.getByText(/1 earlier publish stated a lower value/)).toBeInTheDocument();
  });

  it('carries dark-mode classes beside the light ones', () => {
    render(<slot.Component version={withSplits({ sensitivity: 'not_sensitive' })} />);
    const cls = screen.getByTestId('sensitivity-badge').className;
    expect(cls).toMatch(/bg-green-100/);
    expect(cls).toMatch(/dark:bg-green-500/);
  });

  it('basisWords never names a planning id or an unexplained acronym', () => {
    for (const text of [basisWords(null, null), basisWords('sensitive', { declared: 'sensitive' }), basisWords('not_sensitive', {})]) {
      expect(text).not.toMatch(/FR-|P4\d|WSP|021/);
    }
    expect(slot.title).toContain('weight-space programming (WSP)');
  });
});

describe('ChecksPanel and S-1', () => {
  it('shows the S-1 note like any other outcome (no code change, FTDD 6.8)', () => {
    render(
      <CheckCallout
        outcome={{ check: 'S-1', outcome: 'green', reason: 'Sensitivity: sensitive (private only).', next_step: 'Nothing to do.', evidence: {} }}
      />,
    );
    expect(screen.getByTestId('check-S-1')).toHaveTextContent('S-1 · Passes');
    expect(screen.getByTestId('check-S-1')).toHaveTextContent('Sensitivity: sensitive (private only).');
  });
});

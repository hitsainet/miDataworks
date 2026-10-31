// 004 FR-004.56 (FTASKS 18.4): the label balancer's leakage warning in the Lineage panel.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Lineage } from '@/types/versions';

import { LineagePanel } from './LineagePanel';

const MESSAGE = 'Rows were selected by their label. The class balance of this version is artificial.';

function lineage(extra: Partial<Lineage> = {}): Lineage {
  return {
    version_id: 'v2',
    inputs: [],
    parent_version_id: 'v1',
    children: [],
    steps: [],
    held_out_origin_version_id: null,
    recipe_hash: 'a'.repeat(64),
    recipe_revision_id: 'rev',
    seed: 1,
    rowkey_scheme: 'v1',
    splits: [],
    ...extra,
  };
}

describe('LineagePanel label balancing', () => {
  it('shows the warning for an own and an inherited step', () => {
    render(
      <LineagePanel
        onOpenVersion={() => undefined}
        lineage={lineage({
          label_balancing: [
            { version_id: 'v2', version_number: 2, inherited: false, step_index: 1, operator_version: '1.0.0', label_column: 'label', rows_dropped: 891, warning: { code: 'label_balanced', message: MESSAGE } },
            { version_id: 'v1', version_number: 1, inherited: true, step_index: 3, operator_version: '1.0.0', label_column: 'label', rows_dropped: null, warning: { code: 'label_balanced', message: MESSAGE } },
          ],
        })}
      />,
    );
    const alert = screen.getByRole('alert', { name: 'Label balancing warning' });
    expect(alert).toHaveTextContent(MESSAGE);
    expect(alert).toHaveTextContent('this version: step 1, label_balancer 1.0.0 on label, 891 rows dropped');
    expect(alert).toHaveTextContent('inherited from v1: step 3');
    expect(alert.className).toContain('dark:bg-amber-500/10');
  });

  it('shows nothing when no step balanced by label', () => {
    render(<LineagePanel onOpenVersion={() => undefined} lineage={lineage({ label_balancing: [] })} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

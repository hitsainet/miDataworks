// Dataset card grid (tasks 16.1, 16.2).
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DATASET } from '@/test/fixtures002';

import { DatasetCardGrid } from './DatasetCardGrid';
import { collectCardSlots } from './datasetCardSlots';

describe('DatasetCardGrid', () => {
  it('shows head version, target type, size, rows and state, and opens on click', () => {
    const onOpen = vi.fn();
    render(<DatasetCardGrid datasets={[DATASET]} onOpen={onOpen} slots={[]} />);
    const card = screen.getByTestId('dataset-card');
    for (const text of ['humor-jev9b', 'v2', 'detector', '10,914', '6.0 MB', 'Ready', '1 warning']) expect(card).toHaveTextContent(text);
    expect(screen.getByText('Not published')).toHaveClass('text-amber-700');
    fireEvent.click(card);
    expect(onOpen).toHaveBeenCalledWith(DATASET);
  });

  it("renders feature 008's slot in place of Not published", () => {
    const slots = collectCardSlots({ x: { default: { id: 'publish', order: 1, Component: () => <span>Hub: mistudio/humor@802d806b · Private</span> } } });
    render(<DatasetCardGrid datasets={[DATASET]} onOpen={vi.fn()} slots={slots} />);
    expect(screen.getByText('Hub: mistudio/humor@802d806b · Private')).toBeInTheDocument();
    expect(screen.queryByText('Not published')).not.toBeInTheDocument();
  });
});

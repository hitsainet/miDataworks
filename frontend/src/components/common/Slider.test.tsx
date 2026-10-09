import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Slider } from './Slider';

// The slider's shown value is DERIVED (the prop, or the pointer while dragging) rather than synced
// by an effect; these pin the two behaviours the old effect provided (eslint 10 / react-hooks 7).
describe('Slider', () => {
  it('shows an external value change at once when not dragging', () => {
    const { rerender } = render(<Slider value={1} onChange={() => {}} formatValue={(v) => v.toFixed(0)} />);
    expect(screen.getByRole('slider')).toHaveValue('1');
    rerender(<Slider value={7} onChange={() => {}} formatValue={(v) => v.toFixed(0)} />);
    expect(screen.getByRole('slider')).toHaveValue('7');
    expect(screen.getByText((_, el) => el?.tagName === 'SPAN' && el.textContent === '+7')).toBeInTheDocument();
  });

  it('follows the pointer while dragging and reports the last value on release', () => {
    const onChange = vi.fn();
    const onChangeEnd = vi.fn();
    render(<Slider value={10} onChange={onChange} onChangeEnd={onChangeEnd} />);
    const input = screen.getByRole('slider');
    fireEvent.mouseDown(input);
    fireEvent.change(input, { target: { value: '42' } });
    expect(input).toHaveValue('42');
    expect(onChange).toHaveBeenLastCalledWith(42);
    fireEvent.mouseUp(input);
    expect(onChangeEnd).toHaveBeenCalledTimes(1);
    expect(onChangeEnd).toHaveBeenCalledWith(42);
  });

  it('a release without a move reports the prop value', () => {
    const onChangeEnd = vi.fn();
    const { rerender } = render(<Slider value={3} onChange={() => {}} onChangeEnd={onChangeEnd} />);
    rerender(<Slider value={5} onChange={() => {}} onChangeEnd={onChangeEnd} />);
    const input = screen.getByRole('slider');
    fireEvent.mouseDown(input);
    fireEvent.mouseUp(input);
    expect(onChangeEnd).toHaveBeenCalledWith(5);
  });
});

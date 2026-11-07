// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/components/common/Slider.tsx @ 0efff20
// Mode: copy (docs/REUSE.md), recoloured to indigo with light-mode pairs; checked for React 19-only APIs (none: forwardRef only).
import { useState, useCallback, useRef } from 'react';
import type { ChangeEvent } from 'react';

interface SliderProps {
  value: number;
  onChange: (value: number) => void;
  onChangeEnd?: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  label?: string;
  showValue?: boolean;
  formatValue?: (value: number) => string;
  disabled?: boolean;
  className?: string;
}

export function Slider({
  value,
  onChange,
  onChangeEnd,
  min = 0,
  max = 100,
  step = 1,
  label,
  showValue = true,
  formatValue = (v) => v.toFixed(1),
  disabled = false,
  className = '',
}: SliderProps) {
  const [dragValue, setLocalValue] = useState(value);
  const [isDragging, setIsDragging] = useState(false);
  // Use ref to track latest value for onChangeEnd callback
  const latestValueRef = useRef(value);

  // While dragging the thumb follows the pointer; otherwise it shows the prop. Derived rather than
  // synced by an effect (react-hooks/set-state-in-effect), so an external change shows at once.
  const localValue = isDragging ? dragValue : value;

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const newValue = parseFloat(e.target.value);
      setLocalValue(newValue);
      latestValueRef.current = newValue;
      // Real-time update during drag
      onChange(newValue);
    },
    [onChange]
  );

  const handleMouseDown = useCallback(() => {
    // A drag starts from the value shown, which is the prop until now.
    setLocalValue(value);
    latestValueRef.current = value;
    setIsDragging(true);
  }, [value]);

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
    // Use ref to get the latest value
    onChangeEnd?.(latestValueRef.current);
  }, [onChangeEnd]);

  // Calculate fill percentage
  const percentage = ((localValue - min) / (max - min)) * 100;

  return (
    <div className={`w-full ${className}`}>
      {(label || showValue) && (
        <div className="flex items-center justify-between mb-2">
          {label && (
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</span>
          )}
          {showValue && (
            <span className="text-sm font-semibold text-slate-900 dark:text-slate-100 font-mono min-w-[50px] text-right">
              {localValue > 0 ? '+' : ''}
              {formatValue(localValue)}
            </span>
          )}
        </div>
      )}
      <div className="relative">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={localValue}
          onChange={handleChange}
          onMouseDown={handleMouseDown}
          onMouseUp={handleMouseUp}
          onTouchStart={handleMouseDown}
          onTouchEnd={handleMouseUp}
          disabled={disabled}
          className="slider-thumb w-full h-2 bg-slate-200 dark:bg-slate-700/50 rounded-full appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          style={{
            background: `linear-gradient(to right,
              ${localValue >= 0 ? '#6366f1' : '#f87171'} ${Math.min(percentage, 50)}%,
              ${localValue >= 0 ? '#6366f1' : '#f87171'} ${Math.max(percentage, 50)}%,
              rgba(71, 85, 105, 0.5) ${Math.max(percentage, 50)}%,
              rgba(71, 85, 105, 0.5) 100%)`,
          }}
        />
      </div>
    </div>
  );
}

export default Slider;

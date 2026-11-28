// 021 FTASKS 16.1 (FR-021.32): the label step states the input version's sensitivity beside the plan;
// nothing is refused for it.
import { describe, expect, it } from 'vitest';

import { sensitivityLine } from './LabelStep';

describe('sensitivityLine', () => {
  it('states each value in words', () => {
    expect(sensitivityLine('sensitive')).toMatch(/sensitive data: labelling sends its rows to the endpoint/);
    expect(sensitivityLine('not_sensitive')).toBe('This version is declared not sensitive.');
    expect(sensitivityLine(null)).toMatch(/not declared: treat its rows as sensitive/);
  });
});

// The slot glob discovers slot components and never a test file (2026-11-17).
// `versionDetailSlot.wsp.test.tsx` once matched `versionDetailSlot.*.tsx`; the eager glob bundled it,
// with the Vitest runtime, into production, and the UI was a black screen. Vitest evaluates the same
// `import.meta.glob` as the build, so the discovered paths here are the paths the bundle imports.
// Control: drop the `!../**/*.test.tsx` exclusion → this test fails (the wsp test file is discovered).
import { describe, expect, it } from 'vitest';

import { discoveredSlots, slotModulePaths } from './versionSlots';

describe('version slot discovery', () => {
  it('never discovers a test or spec file', () => {
    const paths = slotModulePaths();
    expect(paths.length).toBeGreaterThan(0);
    expect(paths.filter((p) => /\.(test|spec)\.tsx$/.test(p))).toEqual([]);
  });

  it('still discovers the real slots, the WSP slot included', () => {
    const paths = slotModulePaths();
    expect(paths.some((p) => p.endsWith('/versionDetailSlot.wsp.tsx'))).toBe(true);
    expect(discoveredSlots().length).toBeGreaterThan(0);
  });
});

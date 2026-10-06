import { afterEach, describe, expect, it, vi } from 'vitest';

import { safeStorage } from './safeStorage';

describe('safeStorage', () => {
  afterEach(() => vi.restoreAllMocks());

  it('never throws when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceeded');
    });
    expect(safeStorage.getItem('x')).toBeNull();
    expect(() => safeStorage.setItem('x', 'y')).not.toThrow();
  });
});

import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
  // The URL hash outlives a test in jsdom, and a screen may open what it names (#/detector-sets/<id>):
  // every test starts from a URL with no hash (files under the node environment have no window).
  if (typeof window !== 'undefined') window.history.replaceState(null, '', window.location.pathname);
  try {
    window.localStorage.clear();
  } catch {
    // storage may be unavailable; tests that need it set it up themselves
  }
});

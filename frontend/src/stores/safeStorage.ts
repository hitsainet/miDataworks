// Browser storage that never throws (ADR-020 task 12.5): a private window, blocked site data or a
// preview can make localStorage throw on access, and the shell must still render.
import type { StateStorage } from 'zustand/middleware';

export const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return window.localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      window.localStorage.setItem(name, value);
    } catch {
      // not persisted; the choice still applies for this page view
    }
  },
  removeItem: (name) => {
    try {
      window.localStorage.removeItem(name);
    } catch {
      // nothing to remove
    }
  },
};

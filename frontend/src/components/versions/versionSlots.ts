// Version detail slots (FTDD 002 section 6.3): any feature adds a file matching
// src/components/**/versionDetailSlot.*.tsx whose default export is a VersionSlot. Discovered with
// import.meta.glob, so features add panels without editing feature 002 (004 audit, cross-tab and
// profile; 005 histogram; 008 publish state).
import type { ComponentType } from 'react';

import type { Version } from '@/types/versions';

export interface VersionSlot {
  id: string;
  order: number;
  title: string;
  applies: (version: Version) => boolean;
  Component: ComponentType<{ version: Version }>;
}

type Module = { default: VersionSlot };

export function collectSlots(modules: Record<string, unknown>): VersionSlot[] {
  return Object.values(modules)
    .map((m) => (m as Module).default)
    .filter((s): s is VersionSlot => Boolean(s && s.id && s.Component))
    .sort((a, b) => a.order - b.order);
}

// ⚠ TEST FILES ARE EXCLUDED BY NAME. `versionDetailSlot.wsp.test.tsx` matches
// `versionDetailSlot.*.tsx`, so the eager glob bundled it into production: its module-level
// `describe()` pulled the Vitest runtime into the app and threw at load, and the whole UI was a black
// screen (021-P4 deploy, 2026-11-17). Pinned by versionSlots.discovery.test.ts.
const SLOT_MODULES = import.meta.glob(
  ['../**/versionDetailSlot.*.tsx', '!../**/*.test.tsx', '!../**/*.spec.tsx'],
  { eager: true },
);

/** The module paths the glob discovers (for the discovery test). */
export const slotModulePaths = (): string[] => Object.keys(SLOT_MODULES);

export const discoveredSlots = (): VersionSlot[] => collectSlots(SLOT_MODULES);

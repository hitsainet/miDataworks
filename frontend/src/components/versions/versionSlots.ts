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

export const discoveredSlots = (): VersionSlot[] =>
  collectSlots(import.meta.glob('../**/versionDetailSlot.*.tsx', { eager: true }));

// Store-action reachability (task 12.13). The action list is DERIVED FROM THE STORES THEMSELVES
// (every function on getState()), not hand-kept: miStudio's guard once used four hardcoded names,
// so every action added later had no guard at all. Each action must be called from a component or
// hook; removing the call from the component turns this red.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { useApprovalsStore } from '@/stores/approvalsStore';
import { useHealthStore } from '@/stores/healthStore';
import { useJobsStore } from '@/stores/jobsStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUIStore } from '@/stores/uiStore';

const SRC = join(__dirname, '..');
const STORES = {
  jobsStore: useJobsStore,
  approvalsStore: useApprovalsStore,
  settingsStore: useSettingsStore,
  healthStore: useHealthStore,
  uiStore: useUIStore,
};

/** Actions with no component caller yet, each with its reason. Kept short on purpose. */
const NOT_YET_CALLED: Record<string, string> = {
  'uiStore.openModal': 'copied from miLLM with Modal; the first modal arrives with a feature screen',
  'uiStore.addToast': 'reached through useToast() in the same file; features call it',
};

function consumers(): string {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        if (name !== 'stores' && name !== 'test') walk(path);
      } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) files.push(path);
    }
  };
  walk(SRC);
  return files.map((f) => readFileSync(f, 'utf8')).join('\n');
}

describe('store-action reachability', () => {
  const code = consumers();
  for (const [storeName, store] of Object.entries(STORES)) {
    const actions = Object.entries(store.getState())
      .filter(([, value]) => typeof value === 'function')
      .map(([key]) => key);

    it(`${storeName} exposes actions (the derivation sees them)`, () => {
      expect(actions.length).toBeGreaterThan(0);
    });

    for (const action of actions) {
      const id = `${storeName}.${action}`;
      it(`${id} is called from a component or hook`, () => {
        if (NOT_YET_CALLED[id]) return;
        expect(code, `${id} has no caller outside the stores`).toMatch(new RegExp(`\\.${action}\\b`));
      });
    }
  }

  it('the exemption list names only real actions', () => {
    for (const id of Object.keys(NOT_YET_CALLED)) {
      const [store, action] = id.split('.') as [keyof typeof STORES, string];
      expect(typeof (STORES[store].getState() as unknown as Record<string, unknown>)[action]).toBe('function');
    }
  });
});

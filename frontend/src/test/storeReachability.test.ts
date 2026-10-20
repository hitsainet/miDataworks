// Store-action reachability (task 12.13). The action list is DERIVED FROM THE STORES THEMSELVES
// (every function on getState()), not hand-kept: miStudio's guard once used four hardcoded names,
// so every action added later had no guard at all. Each action must be called from a component or
// hook; removing the call from the component turns this red.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { useAgentAccessStore } from '@/stores/agentAccessStore';
import { useApprovalsStore } from '@/stores/approvalsStore';
import { useCalibrationStore } from '@/stores/calibrationStore';
import { useCurationStore } from '@/stores/curationStore';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useDraftsStore } from '@/stores/draftsStore';
import { useGenerationStore } from '@/stores/generationStore';
import { useHealthStore } from '@/stores/healthStore';
import { useJobsStore } from '@/stores/jobsStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useMinimalPairsStore } from '@/stores/minimalPairsStore';
import { useOperatorsStore } from '@/stores/operatorsStore';
import { usePublishStore } from '@/stores/publishStore';
import { useRecipesStore } from '@/stores/recipesStore';
import { useReviewStore } from '@/stores/reviewStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { useUIStore } from '@/stores/uiStore';
import { useVersionsStore } from '@/stores/versionsStore';

const SRC = join(__dirname, '..');
const STORES = {
  jobsStore: useJobsStore,
  approvalsStore: useApprovalsStore,
  agentAccessStore: useAgentAccessStore,
  settingsStore: useSettingsStore,
  healthStore: useHealthStore,
  uiStore: useUIStore,
  datasetsStore: useDatasetsStore,
  versionsStore: useVersionsStore,
  recipesStore: useRecipesStore,
  draftsStore: useDraftsStore,
  sourcesStore: useSourcesStore,
  operatorsStore: useOperatorsStore,
  publishStore: usePublishStore,
  labelRunsStore: useLabelRunsStore,
  labelingStore: useLabelingStore,
  calibrationStore: useCalibrationStore,
  reviewStore: useReviewStore,
  curationStore: useCurationStore,
  detectorSetsStore: useDetectorSetsStore,
  generationStore: useGenerationStore,
  minimalPairsStore: useMinimalPairsStore,
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
        // A CALL or a HANDLER, never a bare reference: a selector `s.${action}` survives deleting
        // the only call site (control V-s1 survived exactly that way). Accepted: `x.action(`, the
        // selected local `action(`, a handler prop `={action}`, or a fallback `|| action`.
        const use = new RegExp(`(\\.|\\b)${action}\\(|=\\{${action}\\}|\\|\\|\\s*${action}\\b`);
        expect(code, `${id} has no caller outside the stores`).toMatch(use);
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

describe('the guard covers every store', () => {
  // A store added to src/stores/ but not to STORES above would have no reachability guard at all;
  // the list of stores is derived from the directory, never remembered.
  it('every zustand store module is in STORES', () => {
    const dir = join(SRC, 'stores');
    const modules = readdirSync(dir)
      .filter((n) => /^[a-zA-Z]+Store\.ts$/.test(n))
      .filter((n) => /\bcreate</.test(readFileSync(join(dir, n), 'utf8')))
      .map((n) => n.replace(/\.ts$/, ''));
    expect(modules.length).toBeGreaterThan(5);
    expect(Object.keys(STORES).sort()).toEqual(modules.sort());
  });
});

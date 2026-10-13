// The guided flow's draft (FR-002.18, FR-002.48): saved to the backend as the user goes
// (debounced 1 s), with a local copy kept as a convenience only — every storage call is wrapped,
// because a private window or blocked storage must not break the flow.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { draftsApi } from '@/api/drafts';
import { safeStorage } from '@/stores/safeStorage';
import type { DraftWrite, RecipeDraft, RecipeRevision } from '@/types/recipes';

export const DRAFT_KEY = 'midataworks-guided-draft';
export const SAVE_DEBOUNCE_MS = 1000;

interface DraftsState {
  draft: RecipeDraft | null;
  saving: boolean;
  error: string | null;
  loadDraft: () => Promise<RecipeDraft | null>;
  updateDraft: (patch: Partial<DraftWrite>) => void;
  flushDraft: () => Promise<void>;
  setStep: (step: string) => void;
  saveAsRevision: (recipeName?: string) => Promise<RecipeRevision | null>;
  discardDraft: () => Promise<void>;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : 'Something went wrong.');

let timer: ReturnType<typeof setTimeout> | null = null;
let pending: Partial<DraftWrite> = {};

function rememberLocally(draft: RecipeDraft | null): void {
  try {
    if (draft) safeStorage.setItem(DRAFT_KEY, JSON.stringify({ id: draft.id, flow_state: draft.flow_state }));
    else safeStorage.removeItem(DRAFT_KEY);
  } catch {
    // a convenience only
  }
}

function localId(): string | null {
  try {
    const raw = safeStorage.getItem(DRAFT_KEY);
    return typeof raw === 'string' ? ((JSON.parse(raw) as { id?: string }).id ?? null) : null;
  } catch {
    return null;
  }
}

export const useDraftsStore = create<DraftsState>()((set, get) => ({
  draft: null,
  saving: false,
  error: null,
  loadDraft: async () => {
    const id = localId();
    try {
      let draft: RecipeDraft | null = null;
      if (id) draft = await draftsApi.get(id).catch(() => null);
      if (!draft) draft = await draftsApi.create({ flow_state: { step: 'import', choices: {} } });
      set({ draft, error: null });
      rememberLocally(draft);
      return draft;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  updateDraft: (patch) => {
    const draft = get().draft;
    if (!draft) return;
    const merged = { ...draft, ...patch } as RecipeDraft;
    set({ draft: merged });
    rememberLocally(merged);
    pending = { ...pending, ...patch };
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void get().flushDraft(), SAVE_DEBOUNCE_MS);
  },
  flushDraft: async () => {
    const draft = get().draft;
    if (timer) clearTimeout(timer);
    timer = null;
    if (!draft || Object.keys(pending).length === 0) return;
    const body: Partial<DraftWrite> = {
      recipe_id: draft.recipe_id,
      dataset_id: draft.dataset_id,
      name: draft.name,
      body: draft.body,
      step_labels: draft.step_labels,
      inputs: draft.inputs,
      flow_state: draft.flow_state,
    };
    pending = {};
    set({ saving: true });
    try {
      const saved = await draftsApi.update(draft.id, body);
      set({ draft: saved, saving: false, error: null });
    } catch (e) {
      set({ saving: false, error: message(e) });
    }
  },
  setStep: (step) => {
    const draft = get().draft;
    if (!draft) return;
    get().updateDraft({ flow_state: { ...draft.flow_state, step } });
  },
  saveAsRevision: async (recipeName) => {
    await get().flushDraft();
    const draft = get().draft;
    if (!draft) return null;
    try {
      const revision = await draftsApi.save(draft.id, recipeName);
      set({ draft: await draftsApi.get(draft.id), error: null });
      return revision;
    } catch (e) {
      set({ error: message(e) });
      return null;
    }
  },
  discardDraft: async () => {
    const draft = get().draft;
    if (draft) await draftsApi.remove(draft.id).catch(() => undefined);
    set({ draft: null });
    rememberLocally(null);
  },
}));

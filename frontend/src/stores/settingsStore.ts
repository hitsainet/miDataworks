// Settings and endpoint roles (ADR-011, ADR-015). Secrets arrive masked and are never decrypted
// here; saving a masked value back leaves the stored secret unchanged (backend task 8.4).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { rolesApi, settingsApi } from '@/api/endpoints';
import type { EndpointRole, EndpointRoleWrite, ModelList, Role, Setting } from '@/types/api';

interface SettingsState {
  settings: Setting[];
  roles: EndpointRole[];
  models: Partial<Record<Role, ModelList>>;
  loaded: boolean;
  error: string | null;
  notice: string | null;
  loadSettings: () => Promise<void>;
  saveSetting: (key: string, value: string) => Promise<boolean>;
  saveRole: (role: Role, body: EndpointRoleWrite) => Promise<boolean>;
  fetchModels: (role: Role, baseUrl?: string) => Promise<void>;
}

const message = (error: unknown) => (error instanceof ApiError ? error.message : 'Something went wrong.');

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  settings: [],
  roles: [],
  models: {},
  loaded: false,
  error: null,
  notice: null,
  loadSettings: async () => {
    try {
      const [settings, roles] = await Promise.all([settingsApi.list(), rolesApi.list()]);
      set({ settings, roles, error: null, loaded: true });
    } catch (error) {
      set({ error: message(error), loaded: true });
    }
  },
  saveSetting: async (key, value) => {
    try {
      await settingsApi.put(key, value);
      set({ notice: 'Saved.', error: null });
      await get().loadSettings();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null });
      return false;
    }
  },
  saveRole: async (role, body) => {
    try {
      await rolesApi.put(role, body);
      set({ notice: `Saved the ${role} endpoint.`, error: null });
      await get().loadSettings();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null });
      return false;
    }
  },
  fetchModels: async (role, baseUrl) => {
    try {
      const list = await rolesApi.fetchModels(role, baseUrl);
      set((state) => ({ models: { ...state.models, [role]: list }, error: null }));
    } catch (error) {
      set({ error: message(error) });
    }
  },
}));

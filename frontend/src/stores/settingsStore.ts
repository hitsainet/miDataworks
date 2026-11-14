// Settings and endpoint roles (ADR-011, ADR-015). Secrets arrive masked and are never decrypted
// here; saving a masked value back leaves the stored secret unchanged (backend task 8.4).
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { rolesApi, settingsApi } from '@/api/endpoints';
import type { EndpointRole, EndpointRoleWrite, EnvironmentListing, ModelList, Role, Setting } from '@/types/api';

interface SettingsState {
  settings: Setting[];
  roles: EndpointRole[];
  models: Partial<Record<Role, ModelList>>;
  loaded: boolean;
  error: string | null;
  notice: string | null;
  /** A save that succeeded but needs the operator's attention (what a move did to the keys). */
  warning: string | null;
  loadSettings: () => Promise<void>;
  saveSetting: (key: string, value: string) => Promise<boolean>;
  saveRole: (role: Role, body: EndpointRoleWrite) => Promise<boolean>;
  /** Feature 014: delete the key stored for one server. */
  deleteKey: (role: Role, origin: string) => Promise<boolean>;
  fetchModels: (role: Role, baseUrl?: string) => Promise<void>;
  /** Feature 012: the Environment card. */
  environment: EnvironmentListing | null;
  environmentError: string | null;
  loadEnvironment: () => Promise<void>;
  /** Saves an override; resolves to the refusal's message, or null when saved. */
  saveEnvironment: (key: string, value: string, reason?: string) => Promise<string | null>;
  /** "Use the environment value": removes the override. */
  resetEnvironment: (key: string, reason?: string) => Promise<string | null>;
}

const message = (error: unknown) => (error instanceof ApiError ? error.message : 'Something went wrong.');

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  settings: [],
  roles: [],
  models: {},
  loaded: false,
  error: null,
  notice: null,
  warning: null,
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
      set({ notice: 'Saved.', error: null, warning: null });
      await get().loadSettings();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null });
      return false;
    }
  },
  saveRole: async (role, body) => {
    try {
      const saved = await rolesApi.put(role, body);
      if (saved.key_notice) {
        // Keys are kept per server (014): say what the move did, not a plain "Saved".
        set({ notice: null, error: null, warning: `Saved the ${role} endpoint. ${saved.key_notice}` });
      } else {
        set({ notice: `Saved the ${role} endpoint.`, error: null, warning: null });
      }
      await get().loadSettings();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null, warning: null });
      return false;
    }
  },
  deleteKey: async (role, origin) => {
    try {
      await rolesApi.deleteKey(role, origin);
      set({ notice: `Deleted the ${role} endpoint's key for ${origin}.`, error: null, warning: null });
      await get().loadSettings();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null, warning: null });
      return false;
    }
  },
  environment: null,
  environmentError: null,
  loadEnvironment: async () => {
    try {
      set({ environment: await settingsApi.environment(), environmentError: null });
    } catch (error) {
      set({ environmentError: message(error) });
    }
  },
  saveEnvironment: async (key, value, reason) => {
    try {
      await settingsApi.put(key, value, reason);
      await Promise.all([get().loadEnvironment(), get().loadSettings()]);
      return null;
    } catch (error) {
      return message(error);
    }
  },
  resetEnvironment: async (key, reason) => {
    try {
      await settingsApi.remove(key, reason);
      await Promise.all([get().loadEnvironment(), get().loadSettings()]);
      return null;
    } catch (error) {
      return message(error);
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

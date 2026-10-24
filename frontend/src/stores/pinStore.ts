// The Settings PIN (feature 014; operator directive 2026-10-20), modelled on miStudio's. Not a
// login: it locks Settings writes, enforced by the backend (403 PIN_REQUIRED). The dialog asks for
// the PIN when a write is refused (api/client.ts retries it once); the Settings PIN card sets,
// changes and removes it, each with the current PIN.
import { create } from 'zustand';

import { ApiError } from '@/api/client';
import { pinApi } from '@/api/endpoints';
import { pinSessionToken, setPinSessionToken } from '@/api/pinSession';
import type { PinStatus } from '@/types/api';

interface PinState {
  status: PinStatus | null;
  error: string | null;
  notice: string | null;
  /** The unlock dialog: open while a refused write waits for the PIN. */
  prompt: { reason: string; error: string | null; busy: boolean } | null;
  loadStatus: () => Promise<void>;
  /** Opens the dialog; resolves true once unlocked, false when cancelled. */
  requestPin: (reason: string) => Promise<boolean>;
  submitPin: (pin: string) => Promise<void>;
  cancelPrompt: () => void;
  setPin: (pin: string, currentPin?: string) => Promise<boolean>;
  removePin: (currentPin: string) => Promise<boolean>;
  /** Ends this tab's session now (the backend's ends by itself after its time). */
  lock: () => void;
}

const message = (error: unknown) => (error instanceof ApiError ? error.message : 'Something went wrong.');

let resolvePrompt: ((ok: boolean) => void) | null = null;

function settle(ok: boolean) {
  const resolve = resolvePrompt;
  resolvePrompt = null;
  resolve?.(ok);
}

export const usePinStore = create<PinState>()((set, get) => ({
  status: null,
  error: null,
  notice: null,
  prompt: null,
  loadStatus: async () => {
    try {
      set({ status: await pinApi.status(), error: null });
    } catch (error) {
      set({ error: message(error) });
    }
  },
  requestPin: (reason) => {
    settle(false); // a dialog already open for an earlier write gives way to this one
    set({ prompt: { reason, error: null, busy: false } });
    return new Promise<boolean>((resolve) => {
      resolvePrompt = resolve;
    });
  },
  submitPin: async (pin) => {
    const prompt = get().prompt;
    if (!prompt) return;
    set({ prompt: { ...prompt, busy: true, error: null } });
    try {
      const session = await pinApi.unlock(pin);
      setPinSessionToken(session.token);
      set({ prompt: null });
      settle(true);
      void get().loadStatus();
    } catch (error) {
      set({ prompt: { ...prompt, busy: false, error: message(error) } });
    }
  },
  cancelPrompt: () => {
    set({ prompt: null });
    settle(false);
  },
  setPin: async (pin, currentPin) => {
    try {
      const session = await pinApi.set(pin, currentPin);
      setPinSessionToken(session.token);
      set({ notice: currentPin ? 'The Settings PIN is changed.' : 'The Settings PIN is set.', error: null });
      await get().loadStatus();
      return true;
    } catch (error) {
      set({ error: message(error), notice: null });
      return false;
    }
  },
  removePin: async (currentPin) => {
    try {
      const status = await pinApi.remove(currentPin);
      setPinSessionToken(null);
      set({ status, notice: 'The Settings PIN is removed. Settings is open to anyone using this page.', error: null });
      return true;
    } catch (error) {
      set({ error: message(error), notice: null });
      return false;
    }
  },
  lock: () => {
    setPinSessionToken(null);
    set({ notice: 'Settings is locked again on this tab.', error: null });
    void get().loadStatus();
  },
}));

/** Whether this tab holds a session token (validity is the server's call). */
export const holdsPinSession = () => pinSessionToken() !== null;

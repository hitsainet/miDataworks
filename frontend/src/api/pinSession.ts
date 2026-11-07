// The Settings PIN session (feature 014; operator directive 2026-10-20). Not a login: the suite has
// no accounts. The backend answers 403 PIN_REQUIRED to a Settings write while a PIN is set and no
// valid session token comes with it; the token travels in X-Settings-Pin-Session.
//
// Kept in memory and in sessionStorage (this tab only, gone when it closes). Storage access can
// throw in a private window, so every touch is guarded; the token then lives in memory only.

export const PIN_SESSION_HEADER = 'X-Settings-Pin-Session';
const STORAGE_KEY = 'midataworks-settings-pin-session';

let token: string | null = null;
let loaded = false;

export function pinSessionToken(): string | null {
  if (!loaded) {
    loaded = true;
    try {
      token = window.sessionStorage.getItem(STORAGE_KEY);
    } catch {
      token = null;
    }
  }
  return token;
}

export function setPinSessionToken(next: string | null): void {
  token = next;
  loaded = true;
  try {
    if (next) window.sessionStorage.setItem(STORAGE_KEY, next);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // memory only for this page view
  }
}

/** Asks the operator for the PIN and unlocks; resolves true when a new session is in place. */
export type PinPrompter = (reason: string) => Promise<boolean>;

let prompter: PinPrompter | null = null;

/** The PIN dialog registers itself here while mounted (App). Returns the unregister function. */
export function registerPinPrompter(next: PinPrompter): () => void {
  prompter = next;
  return () => {
    if (prompter === next) prompter = null;
  };
}

export function currentPinPrompter(): PinPrompter | null {
  return prompter;
}

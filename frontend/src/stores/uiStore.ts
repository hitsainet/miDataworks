// Origin: miServer (Onegaishimas/miServer) admin-ui/src/stores/uiStore.ts @ 0efff20
// Mode: adapt (docs/REUSE.md). Kept: the toast store and theme/sidebar state with persist. Dropped:
// the modal registry's monitoring-pause flags (miServer-specific). Changed: persistence goes through
// safeStorage (never throws), and the theme is applied to <html class="dark">.
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { ModalState, SidebarState, Theme, Toast } from '@/types/ui';

import { safeStorage } from './safeStorage';

interface UIState {
  theme: Theme;
  /** `collapsed` is the remembered choice (persisted); the sidebar shows `selectSidebarCollapsed`. */
  sidebar: SidebarState;
  /** Loaded at a phone width: the sidebar shows the icon rail over the remembered choice until the
   *  operator toggles it by hand. Never persisted, so a phone visit leaves the stored choice alone. */
  sidebarPhoneStart: boolean;
  modal: ModalState;
  toasts: Toast[];
}

interface UIActions {
  toggleTheme: () => void;
  toggleSidebar: () => void;
  openModal: (id: string, props?: Record<string, unknown>) => void;
  closeModal: () => void;
  addToast: (toast: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;
}

const generateId = () => Math.random().toString(36).substring(2, 9);

/**
 * A phone, as the app's styles define one: narrower than Tailwind's `sm` breakpoint (40rem, 640 px),
 * the exact query its `max-sm:` variant emits. At 390 px the expanded 224 px sidebar leaves the page
 * 166 px (less its padding), and controls run off the screen, so a phone starts on the 64 px icon
 * rail (operator, 2026-11-27, as miForge's sidebar does). Exported so the tests quote it once.
 */
export const PHONE_MEDIA = '(width < 40rem)';

/** True when the window is a phone's width; read once, when the app loads. */
export function isPhoneWidth(): boolean {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(PHONE_MEDIA).matches;
  } catch {
    return false;
  }
}

/** What the sidebar shows: the icon rail on a phone's first load, otherwise the remembered choice. */
export const selectSidebarCollapsed = (state: UIState): boolean => state.sidebar.collapsed || state.sidebarPhoneStart;

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

export const useUIStore = create<UIState & UIActions>()(
  persist(
    (set) => ({
      theme: 'dark',
      sidebar: { collapsed: false, mobileOpen: false },
      sidebarPhoneStart: isPhoneWidth(),
      modal: { id: null, props: undefined },
      toasts: [],
      toggleTheme: () =>
        set((state) => {
          const theme: Theme = state.theme === 'dark' ? 'light' : 'dark';
          applyTheme(theme);
          return { theme };
        }),
      // A toggle by hand flips what is shown and is remembered; on a phone's first load the rail is
      // shown over a remembered "expanded", so the first toggle expands it (and remembers that).
      toggleSidebar: () =>
        set((state) => ({
          sidebarPhoneStart: false,
          sidebar: { ...state.sidebar, collapsed: !selectSidebarCollapsed(state) },
        })),
      openModal: (id, props) => set({ modal: { id, props } }),
      closeModal: () => set({ modal: { id: null, props: undefined } }),
      addToast: (toast) => set((state) => ({ toasts: [...state.toasts, { ...toast, id: generateId() }] })),
      removeToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
    }),
    {
      name: 'midataworks-ui-preferences',
      storage: createJSONStorage(() => safeStorage),
      partialize: (state) => ({
        theme: state.theme,
        sidebar: { collapsed: state.sidebar.collapsed, mobileOpen: false },
      }),
      onRehydrateStorage: () => (state) => {
        if (state) applyTheme(state.theme);
      },
    },
  ),
);

export const useToast = () => {
  const addToast = useUIStore((state) => state.addToast);
  return {
    success: (message: string, duration?: number) => addToast({ type: 'success', message, duration }),
    error: (message: string, duration?: number) => addToast({ type: 'error', message, duration }),
    warning: (message: string, duration?: number) => addToast({ type: 'warning', message, duration }),
    info: (message: string, duration?: number) => addToast({ type: 'info', message, duration }),
  };
};

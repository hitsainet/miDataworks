// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/stores/uiStore.ts @ 0efff20
// Mode: adapt (docs/REUSE.md). Kept: the toast store and theme/sidebar state with persist. Dropped:
// the modal registry's monitoring-pause flags (miLLM-specific). Changed: persistence goes through
// safeStorage (never throws), and the theme is applied to <html class="dark">.
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

import type { ModalState, SidebarState, Theme, Toast } from '@/types/ui';

import { safeStorage } from './safeStorage';

interface UIState {
  theme: Theme;
  sidebar: SidebarState;
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

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

export const useUIStore = create<UIState & UIActions>()(
  persist(
    (set) => ({
      theme: 'dark',
      sidebar: { collapsed: false, mobileOpen: false },
      modal: { id: null, props: undefined },
      toasts: [],
      toggleTheme: () =>
        set((state) => {
          const theme: Theme = state.theme === 'dark' ? 'light' : 'dark';
          applyTheme(theme);
          return { theme };
        }),
      toggleSidebar: () =>
        set((state) => ({ sidebar: { ...state.sidebar, collapsed: !state.sidebar.collapsed } })),
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

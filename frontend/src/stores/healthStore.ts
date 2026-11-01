// GET /api/health for the top bar: the app's own resources and the miLLM / miStudio chips.
import { create } from 'zustand';

import { healthApi } from '@/api/endpoints';
import type { Health } from '@/types/api';

interface HealthState {
  health: Health | null;
  unreachable: boolean;
  fetchHealth: () => Promise<void>;
}

export const useHealthStore = create<HealthState>()((set) => ({
  health: null,
  unreachable: false,
  fetchHealth: async () => {
    try {
      set({ health: await healthApi.get(), unreachable: false });
    } catch {
      set({ unreachable: true });
    }
  },
}));

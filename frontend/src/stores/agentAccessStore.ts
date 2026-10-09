// The Agent access card's data, fetched when Settings mounts (010 FTDD section 7). No polling.
import { create } from 'zustand';

import { agentAccessApi } from '@/api/agentAccess';
import { ApiError } from '@/api/client';
import type { AgentAccess } from '@/types/agentAccess';

interface AgentAccessState {
  access: AgentAccess | null;
  error: string | null;
  fetchAccess: () => Promise<void>;
}

export const useAgentAccessStore = create<AgentAccessState>()((set) => ({
  access: null,
  error: null,
  fetchAccess: async () => {
    try {
      set({ access: await agentAccessApi.get(), error: null });
    } catch (error) {
      set({ error: error instanceof ApiError ? error.message : 'Could not load the agent access details.' });
    }
  },
}));

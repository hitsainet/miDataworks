// The Agent access card's data (feature 010). Display only: nothing here is editable (P-08).
import type { AgentAccess } from '@/types/agentAccess';

import { api } from './client';

export const agentAccessApi = {
  get: () => api<AgentAccess>('/api/v1/agent-access'),
};

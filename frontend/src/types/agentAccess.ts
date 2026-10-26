// GET /api/v1/agent-access (feature 010, FTID 5.6): the display-only Agent access card.
export interface AgentAccess {
  mcp_public_url: string | null;
  mcp: {
    reachable: boolean;
    categories: string[] | null;
    reason: string | null;
    unknown_categories?: string[];
  };
  gated_actions: Record<string, string>;
  label_threshold: number;
  label_window_hours: number;
  approval_ttl_hours: number;
  activity: { window: string; identities: string[]; sessions: number; requests: number };
}

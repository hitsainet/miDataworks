// Feature 018 routes the Code rules screen uses (backend/src/api/v1/endpoints/code_rules.py).
// Revoke and Approve again are the operator's alone (agents get 403 agent_forbidden) and ask for
// the Settings PIN when one is set: the client asks for it on 403 PIN_REQUIRED and sends the
// request once more (api/client.ts), the same flow as every Settings write.
import { api, json } from '@/api/client';
import type {
  ApprovalActionResult,
  ApprovalDecision,
  CodeRuleApprovalState,
  CodeRuleDetail,
  CodeRuleSummary,
} from '@/types/codeRules';

const enc = encodeURIComponent;

export const codeRulesApi = {
  list: (params: { behaviourKey?: string; includeArchived?: boolean }) => {
    const q = new URLSearchParams({ limit: '200' });
    if (params.behaviourKey) q.set('behaviour_key', params.behaviourKey);
    if (params.includeArchived) q.set('include_archived', 'true');
    return api<{ items: CodeRuleSummary[]; total: number }>(`/api/v1/code-rules?${q.toString()}`);
  },
  /** A hash's approval state NOW, with its history (newest first); answers for any hash. */
  approvalState: (sha: string) =>
    api<{ code_sha256: string; state: CodeRuleApprovalState; history: ApprovalDecision[] }>(
      `/api/v1/code-rules/approvals?code_sha256=${enc(sha)}`,
    ),
  get: (name: string, version: number) => api<CodeRuleDetail>(`/api/v1/code-rules/${enc(name)}/versions/${version}`),
  revoke: (sha: string, reason: string) =>
    api<ApprovalActionResult>(`/api/v1/code-rules/approvals/${enc(sha)}/revoke`, json('POST', { reason })),
  approveAgain: (sha: string, reason: string) =>
    api<ApprovalActionResult>(`/api/v1/code-rules/approvals/${enc(sha)}/approve`, json('POST', { reason })),
  archive: (name: string) => api<{ archived: boolean }>(`/api/v1/code-rules/${enc(name)}/archive`, json('POST', {})),
  unarchive: (name: string) => api<{ archived: boolean }>(`/api/v1/code-rules/${enc(name)}/unarchive`, json('POST', {})),
  contract: () => api<{ contract_version: string; code_rules_enabled: boolean }>('/api/v1/runner/contract'),
};

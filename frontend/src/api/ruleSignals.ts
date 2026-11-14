// Feature 016 routes: the validator library and rule sets (FTDD 016 section 6.1). Read-only on this
// screen (authoring is through MCP and REST, D22), plus the stateless preview.
import { api, json } from '@/api/client';
import type {
  PreviewResult,
  RuleDerivation,
  RuleSetDetail,
  RuleSetSummary,
  ValidatorDetail,
  ValidatorSummary,
} from '@/types/ruleSignals';

const enc = encodeURIComponent;

export const ruleSignalsApi = {
  listRuleSets: (params: { behaviourKey?: string; includeArchived?: boolean }) => {
    const q = new URLSearchParams({ limit: '200' });
    if (params.behaviourKey) q.set('behaviour_key', params.behaviourKey);
    if (params.includeArchived) q.set('include_archived', 'true');
    return api<{ items: RuleSetSummary[]; total: number }>(`/api/v1/rule-sets?${q.toString()}`);
  },
  getRuleSet: (name: string, version?: number) =>
    api<RuleSetDetail>(`/api/v1/rule-sets/${enc(name)}${version ? `?version=${version}` : ''}`),
  listValidators: () => api<{ items: ValidatorSummary[]; total: number }>('/api/v1/validators?limit=200'),
  getValidator: (id: string, version: number) =>
    api<ValidatorDetail>(`/api/v1/validators/${enc(id)}/versions/${version}`),
  getDerivation: (id: string) => api<RuleDerivation>(`/api/v1/rule-derivations/${enc(id)}`),
  /** The text is never stored or logged by the server (FR-016.19). */
  preview: (name: string, version: number, text: string) =>
    api<PreviewResult>('/api/v1/rule-sets/preview', json('POST', { name, version, text })),
};

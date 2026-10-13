// Feature 007's API (FTDD 007 section 5.1). The browser never calls miLLM: profiles are read and
// settings compared through the backend.
import { api, json } from '@/api/client';
import type {
  CompareResult,
  DiversityReport,
  GenerationPair,
  GenerationRecord,
  GenerationRun,
  GenerationTemplate,
  IndependenceResult,
  Page,
  Plan,
  Preview,
  RunCreate,
  SteeringSetting,
  TemplateClone,
  TemplateCreate,
} from '@/types/generation';

const runs = '/api/v1/generation-runs';

export const generationApi = {
  templates: (kind?: 'expand' | 'respond') =>
    api<Page<GenerationTemplate>>(`/api/v1/generation-templates${kind ? `?kind=${kind}` : ''}`),
  createTemplate: (body: TemplateCreate) => api<GenerationTemplate>('/api/v1/generation-templates', json('POST', body)),
  cloneTemplate: (id: string, body: TemplateClone) =>
    api<GenerationTemplate>(`/api/v1/generation-templates/${id}/clone`, json('POST', body)),
  plan: (body: RunCreate) => api<Plan>(`${runs}/plan`, json('POST', body)),
  start: (body: RunCreate) => api<GenerationRun>(runs, json('POST', body)),
  list: () => api<Page<GenerationRun>>(`${runs}?limit=50`),
  get: (id: string) => api<GenerationRun>(`${runs}/${id}`),
  records: (id: string, outcome?: string) =>
    api<Page<GenerationRecord>>(`${runs}/${id}/records?limit=50${outcome ? `&outcome=${outcome}` : ''}`),
  pairs: (id: string) => api<Page<GenerationPair>>(`${runs}/${id}/pairs?limit=50`),
  cancel: (id: string) => api<GenerationRun>(`${runs}/${id}/cancel`, json('POST')),
  resume: (id: string) => api<GenerationRun>(`${runs}/${id}/resume`, json('POST')),
  preview: (prompts: string[], setting: SteeringSetting, respond_template_id?: string | null) =>
    api<Preview>(`${runs}/preview`, json('POST', { prompts, setting, respond_template_id: respond_template_id ?? null })),
  compare: (setting_a: SteeringSetting, setting_b: SteeringSetting) =>
    api<CompareResult>('/api/v1/steering-settings/compare', json('POST', { setting_a, setting_b })),
  independence: (body: { mode: string; generator_setting?: SteeringSetting; setting_a?: SteeringSetting; setting_b?: SteeringSetting }) =>
    api<IndependenceResult>(`${runs}/independence-check`, json('POST', body)),
  candidate: (id: string) => api<{ job_id?: string; id?: string }>(`${runs}/${id}/candidate-build`, json('POST', {})),
  diversity: (versionId: string) => api<DiversityReport>(`/api/v1/versions/${versionId}/diversity`),
  requestDiversity: (versionId: string) =>
    api<{ job_id: string; column: string }>(`/api/v1/versions/${versionId}/diversity`, json('POST', {})),
  audit: (versionId: string) =>
    api<unknown>(`/api/v1/versions/${versionId}/audit`, json('POST', { size: 100, strata_columns: ['_dw_origin', 'generation_side'], question: 'Is each generated row correct and safe to train on?' })),
};

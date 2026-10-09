// Feature 005's REST calls (FTDD 005 section 5.1). One function per route.
import { api, json } from '@/api/client';
import type {
  ApprovalAccepted,
  CalibrationStatus,
  DecisionTemplate,
  EndpointTestResult,
  KeepShareResult,
  LabelPage,
  LabelRun,
  LabelRunList,
  LabelRunStart,
  Plan,
  ProbeList,
  ReproductionLink,
  ReproductionLinkStart,
  Rubric,
  RubricBody,
  RubricCreate,
  RubricExport,
  SampleResult,
} from '@/types/labeling';

const runs = '/api/v1/label-runs';

export const labelingApi = {
  templates: () => api<DecisionTemplate[]>('/api/v1/decision-templates'),
  rubrics: () => api<Rubric[]>('/api/v1/rubrics'),
  createRubric: (body: RubricCreate) => api<Rubric>('/api/v1/rubrics', json('POST', body)),
  /** The document is sent as read from the file; the backend validates it before saving. */
  importRubric: (document: unknown) => api<Rubric>('/api/v1/rubrics/import', json('POST', document)),
  cloneRubric: (id: string, body: RubricBody | null) => api<Rubric>(`/api/v1/rubrics/${id}/clone`, json('POST', { body })),
  exportRubric: (id: string) => api<RubricExport>(`/api/v1/rubrics/${id}/export`),
  probes: () => api<ProbeList>('/api/v1/labeling/probes'),
  testEndpoint: (role: string) => api<EndpointTestResult>(`/api/v1/endpoint-roles/${role}/test`, json('POST')),
  sample: (body: Record<string, unknown>) => api<SampleResult>('/api/v1/labeling/sample', json('POST', body)),
  startKeepShare: (body: Record<string, unknown>) =>
    api<{ job_id: string; room: string }>('/api/v1/labeling/keep-share', json('POST', body)),
  keepShare: (jobId: string) => api<KeepShareResult>(`/api/v1/labeling/keep-share/${jobId}`),
  plan: (body: LabelRunStart) => api<Plan>(`${runs}/plan?request=${encodeURIComponent(JSON.stringify(body))}`),
  start: (body: LabelRunStart) => api<LabelRun | ApprovalAccepted>(runs, json('POST', body)),
  list: (params: { input_version_id?: string; page?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.input_version_id) q.set('input_version_id', params.input_version_id);
    if (params.page) q.set('page', String(params.page));
    const qs = q.toString();
    return api<LabelRunList>(qs ? `${runs}?${qs}` : runs);
  },
  get: (id: string) => api<LabelRun>(`${runs}/${id}`),
  labels: (id: string, outcome?: string, page = 1) =>
    api<LabelPage>(`${runs}/${id}/labels?limit=500&page=${page}${outcome ? `&outcome=${encodeURIComponent(outcome)}` : ''}`),
  cancel: (id: string) => api<LabelRun>(`${runs}/${id}/cancel`, json('POST')),
  resume: (id: string) => api<LabelRun>(`${runs}/${id}/resume`, json('POST')),
  rederive: (id: string, body: { threshold_positive: number; threshold_negative: number }) =>
    api<LabelRun>(`${runs}/${id}/rederive`, json('POST', body)),
  createLink: (body: ReproductionLinkStart) => api<ReproductionLink | ApprovalAccepted>('/api/v1/reproduction-links', json('POST', body)),
  links: (mistudioProbeId: string) =>
    api<{ items: ReproductionLink[] }>(`/api/v1/reproduction-links?mistudio_probe_id=${encodeURIComponent(mistudioProbeId)}`),
  calibrationStatus: (labelerIdentityHash: string) =>
    api<CalibrationStatus>(`/api/v1/calibration-status?labeler=${encodeURIComponent(labelerIdentityHash)}`),
};

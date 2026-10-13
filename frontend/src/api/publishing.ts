// Feature 008's calls. Stores call these; components call stores.
import type {
  BuildAccepted,
  CardDraft,
  CheckRun,
  ExportRecord,
  ExportRequest,
  ModelTerms,
  PublishAccepted,
  PublishBuild,
  PublishRecord,
  TermsNote,
  Visibility,
} from '@/types/publishing';

import { api, json } from './client';

const v = (id: string) => `/api/v1/versions/${encodeURIComponent(id)}`;
/** A model ID keeps its slash (``org/name``); each segment is encoded. */
const model = (id: string) => id.split('/').map(encodeURIComponent).join('/');

export const publishingApi = {
  startBuild: (versionId: string, labelColumn: string | null) =>
    api<BuildAccepted>(`${v(versionId)}/publish-builds`, json('POST', { label_column: labelColumn })),
  getBuild: (id: string) => api<PublishBuild>(`/api/v1/publish-builds/${encodeURIComponent(id)}`),
  startChecks: (versionId: string, body: { build_id: string; repo_id: string; visibility: Visibility }) =>
    api<{ check_run_id: string; job_id: string }>(`${v(versionId)}/publish-checks`, json('POST', body)),
  getCheckRun: (id: string) => api<CheckRun>(`/api/v1/publish-check-runs/${encodeURIComponent(id)}`),
  cardDraft: (versionId: string, buildId: string, repoId: string) =>
    api<CardDraft>(`${v(versionId)}/card-draft?build_id=${encodeURIComponent(buildId)}&repo_id=${encodeURIComponent(repoId)}`),
  publish: (body: { version_id: string; build_id: string; repo_id: string; visibility: Visibility; card_prose: string }) =>
    api<PublishAccepted>('/api/v1/publishes', json('POST', body)),
  reverify: (id: string) => api<{ job_id: string }>(`/api/v1/publishes/${encodeURIComponent(id)}/reverify`, json('POST', {})),
  listPublishes: () => api<{ items: PublishRecord[]; total: number }>('/api/v1/publishes?limit=50'),
  getPublish: (id: string) => api<PublishRecord>(`/api/v1/publishes/${encodeURIComponent(id)}`),
  startExport: (body: ExportRequest) => api<{ export_id: string; job_id: string }>('/api/v1/exports', json('POST', body)),
  listExports: () => api<{ items: ExportRecord[]; total: number }>('/api/v1/exports?limit=50'),
  modelTerms: (modelId: string) => api<ModelTerms>(`/api/v1/model-terms/${model(modelId)}`),
  addTermsNote: (modelId: string, body: { training_on_outputs: string; text: string }) =>
    api<TermsNote>(`/api/v1/model-terms/${model(modelId)}/notes`, json('POST', body)),
};

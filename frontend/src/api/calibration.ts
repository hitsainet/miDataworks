// Feature 006 calibration routes (FTDD 006 section 5.1).
import { api, json } from '@/api/client';
import type {
  CalibrationRecord,
  CalibrationSet,
  CalibrationSetImport,
  CalibrationSetPreview,
  CalibrationStatus,
  JobStarted,
  TargetOut,
  TargetsOut,
} from '@/types/calibration';

export const calibrationApi = {
  records: () => api<{ items: CalibrationRecord[]; total: number }>('/api/v1/calibration-records?limit=200'),
  record: (id: string) => api<CalibrationRecord>(`/api/v1/calibration-records/${encodeURIComponent(id)}`),
  sets: () => api<{ items: CalibrationSet[]; total: number }>('/api/v1/calibration-sets?limit=200'),
  preview: (body: CalibrationSetImport) => api<CalibrationSetPreview>('/api/v1/calibration-sets/preview', json('POST', body)),
  importSet: (body: CalibrationSetImport) => api<CalibrationSet>('/api/v1/calibration-sets/import', json('POST', body)),
  fromReview: (queueId: string) => api<CalibrationSet>('/api/v1/calibration-sets/from-review', json('POST', { queue_id: queueId })),
  compute: (labelRunId: string, calibrationSetId: string) =>
    api<JobStarted>('/api/v1/calibration-records', json('POST', { label_run_id: labelRunId, calibration_set_id: calibrationSetId })),
  status: (identityHash: string) => api<CalibrationStatus>(`/api/v1/calibration-status?labeler=${encodeURIComponent(identityHash)}`),
  targets: (questionHash: string) => api<TargetsOut>(`/api/v1/calibration-targets?question_hash=${encodeURIComponent(questionHash)}`),
  setTarget: (question: string, target: number) => api<TargetOut>('/api/v1/calibration-targets', json('PUT', { question, target })),
};

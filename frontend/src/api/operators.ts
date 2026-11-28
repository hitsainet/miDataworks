// Feature 003's routes (FTDD 003 section 5.1). Previews answer 200 or 202; poll() follows a 202.
import { api, json } from '@/api/client';
import type {
  AllowlistChange,
  AllowlistItem,
  OperatorEntry,
  OperatorList,
  PreviewAnswer,
  PreviewRequest,
  PreviewResult,
  SchemaSubset,
  StatisticsResult,
} from '@/types/operators';

const BASE = '/api/v1/operators';
const enc = encodeURIComponent;

export interface ListFilters {
  provider?: string;
  kind?: string;
  state?: string;
}

function query(filters: ListFilters): string {
  const params = new URLSearchParams({ limit: '200' });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return params.toString();
}

export const POLL_MS = 500;

async function poll<T>(answer: PreviewAnswer<T>, sleep: (ms: number) => Promise<void>): Promise<T> {
  let current = answer;
  for (let i = 0; i < 400 && current.status !== 'done'; i++) {
    await sleep(POLL_MS);
    current = await api<PreviewAnswer<T>>(`${BASE}/previews/${enc(current.preview_id)}`);
  }
  if (current.status !== 'done') throw new Error('The preview did not finish; run it again on a smaller sample.');
  return current.result;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export const operatorsApi = {
  list: (filters: ListFilters = {}) => api<OperatorList>(`${BASE}?${query(filters)}`),
  get: (name: string, version: string) => api<OperatorEntry>(`${BASE}/${enc(name)}/${enc(version)}`),
  versions: (name: string) => api<{ name: string; current_version: string | null; versions: OperatorEntry[] }>(`${BASE}/${enc(name)}`),
  schemaSubset: () => api<SchemaSubset>(`${BASE}/schema-subset`),
  validate: (name: string, version: string, params: Record<string, unknown>) =>
    api<{ valid: true }>(`${BASE}/${enc(name)}/${enc(version)}/validate`, json('POST', { params })),
  preview: async (name: string, version: string, request: PreviewRequest, sleep = wait) =>
    poll(await api<PreviewAnswer<PreviewResult>>(`${BASE}/${enc(name)}/${enc(version)}/preview`, json('POST', request)), sleep),
  statistics: async (name: string, version: string, request: PreviewRequest, sleep = wait) =>
    poll(await api<PreviewAnswer<StatisticsResult>>(`${BASE}/${enc(name)}/${enc(version)}/statistics`, json('POST', request)), sleep),
  allowlist: () => api<{ items: AllowlistItem[] }>(`${BASE}/allowlist`),
  allow: (change: AllowlistChange) => api<unknown>(`${BASE}/allowlist`, json('POST', change)),
  revoke: (change: AllowlistChange) => api<unknown>(`${BASE}/allowlist/revoke`, json('POST', change)),
};

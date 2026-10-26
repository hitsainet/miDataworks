// Feature 012: the Environment card on the Settings screen. The card runs its real store and API
// client against fetch stubs; payloads and call counts are asserted.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { useSettingsStore } from '@/stores/settingsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import type { EnvironmentRow } from '@/types/api';

import { EnvironmentCard } from './EnvironmentCard';

const row = (over: Partial<EnvironmentRow>): EnvironmentRow => ({
  key: 'x', env_var: 'X', label: 'X', description: 'd', group: 'runtime', type: 'int', value: 1, env_value: 1, source: 'env',
  runtime_writable: true, agent_writable: true, effect: 'next request', effect_detail: null, restart: null, caution: null,
  secret: false, is_set: true, minimum: null, maximum: null, exclusive_minimum: 0, exclusive_maximum: null, choices: null,
  updated_at: null, updated_by: null, ...over,
});

const ENVIRONMENT = {
  settings: [
    row({ key: 'upload_max_bytes', env_var: 'UPLOAD_MAX_BYTES', label: 'Largest upload, in bytes', value: 2147483648, env_value: 2147483648 }),
    row({ key: 'label_transient_retries', env_var: 'LABEL_TRANSIENT_RETRIES', label: 'Retries of a temporary endpoint error', value: 7, env_value: 3, source: 'override', effect: 'next run', updated_by: 'Ada', updated_at: '2026-10-20T10:00:00Z', exclusive_minimum: null, minimum: 0 }),
    row({ key: 'log_level', env_var: 'LOG_LEVEL', label: 'Log detail', type: 'choice', value: 'INFO', env_value: 'INFO', effect: 'now', choices: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], exclusive_minimum: null }),
    row({ key: 'millm_steering_supported', env_var: 'MILLM_STEERING_SUPPORTED', label: 'Allow steered generation', type: 'bool', value: true, env_value: true, agent_writable: false, caution: 'Off refuses steered generation.', exclusive_minimum: null }),
    row({ key: 'janitor_interval_seconds', env_var: 'JANITOR_INTERVAL_SECONDS', group: 'restart', runtime_writable: false, agent_writable: false, effect: null, value: 120, restart: 'Restart the midataworks-backend pod (this stops every running job).' }),
    row({ key: 'approval_ttl_hours', env_var: 'APPROVAL_TTL_HOURS', group: 'approval_rule', runtime_writable: false, agent_writable: false, effect: null, value: 24 }),
    row({ key: 'settings_encryption_key', env_var: 'SETTINGS_ENCRYPTION_KEY', group: 'environment', type: 'string', runtime_writable: false, agent_writable: false, effect: null, value: null, env_value: null, secret: true, is_set: true }),
    row({ key: 'database_url', env_var: 'DATABASE_URL', group: 'environment', type: 'string', runtime_writable: false, agent_writable: false, effect: null, value: 'postgresql+asyncpg://dw:***@db/x', env_value: 'postgresql+asyncpg://dw:***@db/x' }),
  ],
  history: [
    { key: 'label_transient_retries', previous_value: null, new_value: '7', actor: 'Ada', origin: 'operator', approval_id: null, reason: 'flaky network', changed_at: '2026-10-20T10:00:00Z' },
  ],
};

beforeEach(() => useSettingsStore.setState({ environment: null, environmentError: null }));
afterEach(() => vi.unstubAllGlobals());

const routes = (extra: Record<string, unknown> = {}) => ({
  'GET /api/v1/settings/environment': ENVIRONMENT,
  'GET /api/v1/settings': [],
  'GET /api/v1/endpoint-roles': [],
  ...extra,
});

describe('EnvironmentCard (feature 012)', () => {
  it('shows each group: runtime rows with effect, values and last change; read-only groups; secrets as set', async () => {
    mockFetch(routes());
    render(<EnvironmentCard />);
    const retries = await screen.findByTestId('env-row-label_transient_retries');
    expect(within(retries).getByTestId('env-effect-label_transient_retries')).toHaveTextContent('Takes effect: next run');
    expect(within(retries).getByTestId('env-source-label_transient_retries')).toHaveTextContent('changed here');
    expect(within(retries).getByTestId('env-values-label_transient_retries')).toHaveTextContent('In force: 7 · Environment value: 3 · Last changed by Ada');
    expect(within(retries).getByRole('button', { name: 'Use the environment value' })).toBeInTheDocument();
    expect(within(screen.getByTestId('env-row-upload_max_bytes')).queryByRole('button', { name: 'Use the environment value' })).toBeNull();
    expect(within(screen.getByTestId('env-row-millm_steering_supported')).getByText('operator only')).toBeInTheDocument();
    expect(screen.getByTestId('env-restart')).toHaveTextContent('Restart the midataworks-backend pod');
    expect(screen.getByTestId('env-approval-rules')).toHaveTextContent('APPROVAL_TTL_HOURS');
    expect(within(screen.getByTestId('env-from-environment')).getByTestId('env-ro-settings_encryption_key')).toHaveTextContent('set');
    expect(screen.getByTestId('env-history')).toHaveTextContent('LABEL_TRANSIENT_RETRIES: environment value → 7 by Ada');
    expect(screen.getByTestId('env-history')).toHaveTextContent('flaky network');
  });

  it('saves a new value with its reason, once, then reads the listing again', async () => {
    const { calls } = mockFetch(routes({ 'PUT /api/v1/settings/log_level': { key: 'log_level' } }));
    render(<EnvironmentCard />);
    const input = await screen.findByTestId('env-input-log_level');
    fireEvent.change(input, { target: { value: 'DEBUG' } });
    fireEvent.change(screen.getByTestId('env-reason-log_level'), { target: { value: 'chasing a bug' } });
    fireEvent.click(screen.getByTestId('env-save-log_level'));
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/settings/environment')).toHaveLength(2));
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([
      { method: 'PUT', path: '/api/v1/settings/log_level', body: { value: 'DEBUG', reason: 'chasing a bug' } },
    ]);
  });

  it('enters a byte limit in GB and refuses 0 (T-02 kept)', async () => {
    const { calls } = mockFetch(routes({ 'PUT /api/v1/settings/upload_max_bytes': { key: 'upload_max_bytes' } }));
    render(<EnvironmentCard />);
    const input = await screen.findByLabelText('Largest upload, in GB');
    expect(input).toHaveValue(2);
    fireEvent.change(input, { target: { value: '0' } });
    expect(screen.getByTestId('env-bytes-upload_max_bytes')).toHaveTextContent('Enter a size above 0 GB.');
    expect(screen.getByTestId('env-save-upload_max_bytes')).toBeDisabled();
    fireEvent.change(input, { target: { value: '3' } });
    expect(screen.getByTestId('env-bytes-upload_max_bytes')).toHaveTextContent('Saves 3,221,225,472 bytes.');
    fireEvent.click(screen.getByTestId('env-save-upload_max_bytes'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.filter((c) => c.method === 'PUT')).toEqual([{ method: 'PUT', path: '/api/v1/settings/upload_max_bytes', body: { value: '3221225472' } }]);
  });

  it('"Use the environment value" sends one DELETE with the reason', async () => {
    const { calls } = mockFetch(routes({ 'DELETE /api/v1/settings/label_transient_retries': () => ({ status: 204 }) }));
    render(<EnvironmentCard />);
    await screen.findByTestId('env-row-label_transient_retries');
    fireEvent.change(screen.getByTestId('env-reason-label_transient_retries'), { target: { value: 'network fixed' } });
    fireEvent.click(screen.getByTestId('env-reset-label_transient_retries'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual(['/api/v1/settings/label_transient_retries?reason=network%20fixed']);
  });

  it("shows the server's refusal on its row", async () => {
    mockFetch(routes({
      'PUT /api/v1/settings/label_transient_retries': () => ({ status: 422, json: { error: { code: 'INVALID_SETTING_VALUE', message: 'label_transient_retries was not changed: LABEL_TRANSIENT_RETRIES: Input should be greater than or equal to 0', details: {} } } }),
    }));
    render(<EnvironmentCard />);
    const input = await screen.findByTestId('env-input-label_transient_retries');
    fireEvent.change(input, { target: { value: '-1' } });
    fireEvent.click(screen.getByTestId('env-save-label_transient_retries'));
    expect(await screen.findByTestId('env-error-label_transient_retries')).toHaveTextContent('greater than or equal to 0');
  });
});

describe('the Environment card is on the Settings screen of the real app', () => {
  it('#/settings renders it (removing it from SettingsPanel turns this red)', async () => {
    mockFetch({ ...routes(), 'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] }, 'GET /api/v1/approvals': { approvals: [] } });
    window.history.replaceState(null, '', '#/settings');
    render(<App />);
    expect(await screen.findByTestId('environment-card')).toBeInTheDocument();
    expect(await screen.findByTestId('env-row-upload_max_bytes')).toBeInTheDocument();
    window.history.replaceState(null, '', '#/');
  });
});

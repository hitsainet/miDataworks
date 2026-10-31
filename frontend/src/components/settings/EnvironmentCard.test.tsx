// Feature 012: the Environment card on the Settings screen. The card runs its real store and API
// client against fetch stubs; payloads and call counts are asserted.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { useSettingsStore } from '@/stores/settingsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import type { EnvironmentRow } from '@/types/api';

import { EnvironmentCard } from './EnvironmentCard';
import { SECTION_ORDER, sectionOf, sectionRows } from './environmentSections';

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

beforeEach(() => useSettingsStore.setState({ environment: null, environmentError: null, roles: [] }));
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

// 2026-10-28: the list was one flat run of 72 runtime rows in the backend's order. Rows are now
// grouped by the part of miDataworks they belong to, sections in a fixed order, labels A to Z.
describe('the Environment card groups and orders its rows', () => {
  const sections = (testId: string) =>
    within(screen.getByTestId(testId)).getAllByTestId(`${testId}-section`).map((el) => el.getAttribute('data-section'));
  const keysIn = (testId: string, section: string) => {
    const el = within(screen.getByTestId(testId)).getAllByTestId(`${testId}-section`).find((e) => e.getAttribute('data-section') === section)!;
    return Array.from(el.querySelectorAll('[data-testid^="env-row-"], [data-testid^="env-ro-"]')).map((e) => e.getAttribute('data-testid')!.replace(/^env-(row|ro)-/, ''));
  };
  // Given out of order on purpose: the backend's order must not be what the operator reads.
  const GROUPED = {
    settings: [
      row({ key: 'upload_max_bytes', env_var: 'UPLOAD_MAX_BYTES', label: 'Largest upload, in bytes', value: 1, env_value: 1 }),
      row({ key: 'label_transient_retries', env_var: 'LABEL_TRANSIENT_RETRIES', label: 'Retries of a temporary endpoint error' }),
      row({ key: 'classifier_spend_ceiling_usd', env_var: 'CLASSIFIER_SPEND_CEILING_USD', label: 'Total US dollars paid classifier runs may spend', type: 'float', value: 5, agent_writable: false }),
      row({ key: 'code_rules_enabled', env_var: 'CODE_RULES_ENABLED', label: 'Run operator code rules', type: 'bool', value: true }),
      row({ key: 'classifier_run_cap_rows', env_var: 'CLASSIFIER_RUN_CAP_ROWS', label: 'Most rows one paid classifier run may score' }),
      row({ key: 'millm_base_url', env_var: 'MILLM_BASE_URL', label: 'miLLM address', type: 'address', value: 'http://m' }),
      row({ key: 'import_confirm_bytes', env_var: 'IMPORT_CONFIRM_BYTES', label: 'Imports above this many bytes need a confirmation' }),
      row({ key: 'brand_new_knob', env_var: 'BRAND_NEW_KNOB', label: 'A setting no rule names yet' }),
      row({ key: 'redis_url', env_var: 'REDIS_URL', group: 'environment', type: 'string', label: 'Redis url', value: 'redis://r', runtime_writable: false }),
      row({ key: 'curation_audit_folds', env_var: 'CURATION_AUDIT_FOLDS', group: 'environment', label: 'Curation audit folds', runtime_writable: false }),
      row({ key: 'curation_control_runs', env_var: 'CURATION_CONTROL_RUNS', group: 'environment', label: 'Curation control runs', runtime_writable: false }),
      row({ key: 'app_build', env_var: 'APP_BUILD', group: 'environment', type: 'string', label: 'App build', value: 'abc', runtime_writable: false }),
    ],
    history: [],
  };

  it('shows each group by section, sections in the fixed order, rows by label within each', async () => {
    mockFetch(routes({ 'GET /api/v1/settings/environment': GROUPED }));
    render(<EnvironmentCard />);
    await screen.findByTestId('env-row-code_rules_enabled');
    expect(sections('env-runtime')).toEqual([
      'Connections to other apps and services',
      'Imports and storage',
      'Labeling and paid endpoints',
      'Code rules',
      'Other',
    ]);
    expect(keysIn('env-runtime', 'Imports and storage')).toEqual(['import_confirm_bytes', 'upload_max_bytes']);
    // "Most rows ...", "Retries ...", "Total ...": by label, not by key or by the order received
    expect(keysIn('env-runtime', 'Labeling and paid endpoints')).toEqual(['classifier_run_cap_rows', 'label_transient_retries', 'classifier_spend_ceiling_usd']);
    expect(keysIn('env-runtime', 'Other')).toEqual(['brand_new_knob']);
    expect(sections('env-environment-list')).toEqual(['Connections to other apps and services', 'Curation and diversity', 'Jobs, logging and the deployment']);
    expect(keysIn('env-environment-list', 'Curation and diversity')).toEqual(['curation_audit_folds', 'curation_control_runs']);
  });

  it('sectionRows keeps every row exactly once and orders sections by SECTION_ORDER', () => {
    const rows = GROUPED.settings;
    const out = sectionRows(rows);
    expect(out.flatMap((s) => s.rows.map((r) => r.key)).sort()).toEqual(rows.map((r) => r.key).sort());
    const order = out.map((s) => SECTION_ORDER.indexOf(s.section));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    for (const s of out) {
      const labels = s.rows.map((r) => r.label.toLowerCase());
      expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b)));
    }
  });

  it('places the keys the deployment has today in a named section (none falls to Other)', () => {
    // The keys GET /api/v1/settings/environment returned on 2026-10-28. A new key lands in Other
    // (never hidden) until a rule names it.
    const LIVE = 'agent_label_row_threshold agent_label_window_hours approval_ttl_hours detector_length_overlap_min judge_parse_failure_min_rows judge_parse_failure_stop_share app_build calibration_max_per_rater_rows celery_task_always_eager curation_audit_folds curation_cell_sample_size curation_control_runs curation_control_tolerance_pp curation_embed_batch_size curation_embed_exact_max_rows curation_embed_max_input_chars curation_inline_max_rows curation_minhash_permutations curation_text_baseline_max_rows data_dir database_url datajuicer_health_cache_s dataworks_api_url designer_handoff_key designer_validate_timeout_s detector_capability_ttl_seconds detector_text_separability_rows diversity_bootstrap_resamples diversity_cluster_k diversity_sample_size diversity_sweep_max_attempts environment ephemeral_secret_ttl_s generation_chunk_size generation_engine_path generation_max_n_responses health_probe_timeout_seconds hf_datasets_server_url hf_hub_url internal_api_secret label_batch_max_lines label_chunk_size label_lease_retry_seconds label_sample_timeout_seconds mcp_internal_url mcp_public_url millm_lease_holder operator_batch_rows operator_params_max_bytes operator_preview_result_ttl_s operator_preview_soft_limit_s operator_preview_sync_wait_s operator_test_fixtures orphan_sweep_age_minutes parquet_row_group_rows preview_timeout_s progress_heartbeat_seconds public_api_url publish_build_batch_rows publish_max_file_bytes redis_url review_candidate_max_bytes reward_bundle_trl_version row_event_copy_chunk row_history_text_matches runner_url settings_encryption_key settings_pin_bypass source_profile_max_rows curation_sweep_interval_s diversity_sweep_seconds janitor_interval_seconds operator_preview_hard_limit_s agent_request_retention_days behaviour_max_held_out_values behaviour_max_subquestions behaviour_max_vocabulary_values classifier_run_cap_rows classifier_spend_ceiling_usd code_rule_api_request_s code_rule_call_fuel code_rule_dry_run_max_texts code_rule_dry_run_per_minute code_rule_failure_share code_rule_input_code_points code_rule_load_fuel code_rule_max_cases code_rule_max_functions code_rule_max_param_schema_bytes code_rule_max_source_bytes code_rule_memory_bytes code_rule_output_bytes code_rule_wall_ms code_rules_enabled detector_max_sets_per_send detector_send_download_timeout_minutes detector_send_janitor_limit_seconds detector_send_poll_seconds dj_num_proc endpoint_http_timeout_seconds generation_preview_max_prompts generation_preview_timeout_seconds generation_requests_in_flight hf_http_timeout_s import_confirm_bytes keep_share_max_rows keep_share_sample_rows label_backoff_cap_seconds label_batch_poll_seconds label_max_consecutive_failures label_sample_max_rows label_transient_retries log_level millm_base_url millm_lease_ttl_seconds millm_steering_supported millm_ui_url mistudio_base_url mistudio_timeout_seconds operator_model_preview_sample_default operator_model_preview_sample_max operator_preview_sample_default operator_preview_sample_max publish_default_namespace publish_hub_backoff_s publish_hub_retries publish_janitor_limit_seconds recipe_max_rule_sets review_max_queues_per_handoff rule_derivation_max_signals rule_derivation_max_source_rows rule_derivation_min_held_out_per_class rule_derivation_time_limit_s rule_preview_max_text_bytes rule_set_max_cases rule_set_max_code_params_bytes rule_set_max_document_bytes rule_set_max_signals rule_set_max_terms second_opinion_instructed_min second_opinion_instructed_rate second_opinion_no_rule_negatives source_import_janitor_limit_s upload_max_bytes version_build_janitor_minutes'.split(' ');
    expect(LIVE).toHaveLength(145);
    expect(LIVE.filter((k) => sectionOf(k) === 'Other')).toEqual([]);
    expect(sectionOf('code_rules_enabled')).toBe('Code rules');
    expect(sectionOf('classifier_spend_ceiling_usd')).toBe('Labeling and paid endpoints');
    expect(sectionOf('agent_label_row_threshold')).toBe('Agents and approvals');
  });

  it('shows the endpoint prices read-only beside the spend ceiling, pointing to the endpoint card', async () => {
    useSettingsStore.setState({
      roles: [
        { role: 'classifier', configured: true, protocol: 'typesafe_systemone', base_url: 'https://t/v1', model_id: 'm', api_key: null, has_api_key: false, inherit_from_judge: false, use_mode: 'own', effective_role: 'classifier', price_per_call_usd: 0.000015, paid: true },
        { role: 'judge', configured: true, protocol: 'openai_chat', base_url: 'http://m', model_id: 'm', api_key: null, has_api_key: false, inherit_from_judge: false, use_mode: 'own', effective_role: 'judge', price_per_call_usd: null, paid: false },
      ],
    });
    mockFetch(routes({ 'GET /api/v1/settings/environment': GROUPED }));
    render(<EnvironmentCard />);
    await screen.findByTestId('env-row-code_rules_enabled');
    const labeling = within(screen.getByTestId('env-runtime')).getAllByTestId('env-runtime-section').find((e) => e.getAttribute('data-section') === 'Labeling and paid endpoints')!;
    const price = within(labeling).getByTestId('env-price-classifier');
    expect(price).toHaveTextContent('Classifier endpoint price per call, in US dollars$0.000015');
    expect(price).toHaveTextContent('change it on the Classifier endpoint card above');
    expect(within(price).queryByRole('textbox')).toBeNull();
    // an unpaid, unpriced role is not listed
    expect(screen.queryByTestId('env-price-judge')).toBeNull();
  });

  it('says a paid endpoint with no price is refused until one is entered', async () => {
    useSettingsStore.setState({
      roles: [{ role: 'classifier', configured: true, protocol: 'typesafe_systemone', base_url: 'https://t/v1', model_id: 'm', api_key: null, has_api_key: false, inherit_from_judge: false, use_mode: 'own', effective_role: 'classifier', price_per_call_usd: null, paid: true }],
    });
    mockFetch(routes({ 'GET /api/v1/settings/environment': GROUPED }));
    render(<EnvironmentCard />);
    const price = await screen.findByTestId('env-price-classifier');
    expect(price).toHaveTextContent('not entered');
    expect(price).toHaveTextContent('refused until a price (zero included) is entered');
  });
});

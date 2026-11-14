// Feature 020 (FR-020.21; FTASKS 16.3, control M63): the "load the model" message links to
// miServer's Models page at the address health serves (`dependencies.millm.ui_url`, from
// MISERVER_UI_URL), whatever that address is. The component writes no host of its own, so the
// link moves to the public miServer host when the deployment's setting does and never before.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useHealthStore } from '@/stores/healthStore';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import { useLabelingStore } from '@/stores/labelingStore';
import { useVersionsStore } from '@/stores/versionsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { VERSION } from '@/test/fixtures002';
import { PROBE_LIST, TEMPLATE } from '@/test/fixtures005';
import type { Health } from '@/types/api';

import { LabelStep, PLAN_DEBOUNCE_MS } from './LabelStep';

const WAIT = { timeout: PLAN_DEBOUNCE_MS + 3000 };
const MODEL = 'meta-llama/Llama-3.1-8B-Instruct';
const V1 = { ...VERSION, id: 'v1', dataset_name: 'colbert-humor-detection', number: 1, total_rows: 2000, warnings_count: 0 };
const PROBE = PROBE_LIST.items[0];

const withUi = (ui_url: string | undefined): Health =>
  ({ ...HEALTH, dependencies: { ...HEALTH.dependencies, millm: { ...HEALTH.dependencies.millm, ui_url } } }) as unknown as Health;

const routes = () => ({
  'GET /api/v1/decision-templates': [TEMPLATE],
  'GET /api/v1/rubrics': [],
  'GET /api/v1/versions': { items: [V1], total: 1 },
  'GET /api/v1/labeling/probes': { ...PROBE_LIST, resident_model: null },
  'GET /api/v1/label-runs/plan': () => ({
    status: 409,
    json: { error: { code: 'MODEL_NOT_LOADED', message: `No model is loaded in miServer. Load ${MODEL}, then start the run.`, details: { needed: MODEL, resident: null } } },
  }),
  'GET /api/v1/calibration-status': () => ({ status: 404, json: { error: { code: 'NOT_FOUND', message: 'none', details: {} } } }),
});

async function waitForModel(ui_url: string | undefined) {
  useHealthStore.setState({ health: withUi(ui_url) });
  mockFetch(routes());
  render(<LabelStep versionId="v1" />);
  fireEvent.change(screen.getByLabelText('Label with'), { target: { value: 'probe' } });
  await waitFor(() => expect(within(screen.getByLabelText('Probe')).getAllByRole('option').length).toBeGreaterThan(1));
  fireEvent.change(screen.getByLabelText('Probe'), { target: { value: PROBE.probe_id } });
  await screen.findByTestId('model-wait', {}, WAIT);
}

describe('LabelStep: the Models link', () => {
  beforeEach(() => {
    useLabelingStore.setState({ plan: null, planError: null, planErrorCode: null, planErrorDetails: {}, planRefusal: null, probes: null, probesError: null, probesUnconfigured: false, started: null, approval: null, error: null, calibration: {} });
    useLabelRunsStore.setState({ list: [], byId: {}, live: {}, labels: {}, selectedId: null, error: null });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    useVersionsStore.setState({ versionList: [] });
  });

  it.each([
    ['https://miserver.hitsai.net', 'https://miserver.hitsai.net/models'],
    ['https://another-host.example.test/admin/', 'https://another-host.example.test/admin/models'],
  ])('the Models link uses the configured miServer address (%s)', async (configured, href) => {
    await waitForModel(configured);
    const link = await screen.findByTestId('miserver-models-link', {}, WAIT);
    expect(link).toHaveAttribute('href', href);
    expect(link).toHaveTextContent("Open miServer's Models page");
    // The one link in the message, and it names nothing but the configured host.
    expect(within(screen.getByTestId('model-wait')).getAllByRole('link')).toHaveLength(1);
  });

  it('with no configured address there is no link, and the setting is named by its new name', async () => {
    await waitForModel(undefined);
    expect(screen.queryByTestId('miserver-models-link')).toBeNull();
    expect(within(screen.getByTestId('model-wait')).queryAllByRole('link')).toHaveLength(0);
    expect(screen.getByTestId('model-wait')).toHaveTextContent('MISERVER_UI_URL');
  });
});

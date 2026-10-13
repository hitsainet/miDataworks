// Reachability for feature 002's screens (tasks 14.1, 15.1, 16.1, 17.1): each built screen is in the
// registry the shell renders from, and the shell renders it — remove a line and these turn red.
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { CalibrationPanel } from '@/components/panels/CalibrationPanel';
import { DatasetsPanel } from '@/components/panels/DatasetsPanel';
import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { GenerationPanel } from '@/components/panels/GenerationPanel';
import { LabelRunsPanel } from '@/components/panels/LabelRunsPanel';
import { NewDatasetPanel } from '@/components/panels/NewDatasetPanel';
import { RecipesPanel } from '@/components/panels/RecipesPanel';
import { ReviewPanel } from '@/components/panels/ReviewPanel';
import { VersionDetailPanel } from '@/components/panels/VersionDetailPanel';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { META } from '@/test/fixtures002';

import { PANEL_COMPONENTS } from './panelComponents';

const BASE = {
  'GET /api/health': HEALTH,
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/datasets/meta': META,
  'GET /api/v1/datasets': { items: [], total: 0 },
  'GET /api/v1/versions': { items: [], total: 0 },
  'GET /api/v1/recipes': { items: [], total: 0 },
  'GET /api/v1/label-runs': { items: [], total: 0, page: 1, limit: 50 },
  'GET /api/v1/calibration-records': { items: [], total: 0 },
  'GET /api/v1/calibration-sets': { items: [], total: 0 },
  'GET /api/v1/review-queues': { items: [], total: 0 },
  'GET /api/v1/detector-sets': { items: [], total: 0 },
  'GET /api/v1/generation-runs': { items: [], total: 0, page: 1, limit: 50 },
  'POST /api/v1/recipe-drafts': { id: 'draft-1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'goal' }, updated_by: 'Ada', updated_at: '' },
};

describe('feature 002 screens are registered and rendered', () => {
  beforeEach(() => mockFetch(BASE));
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = '';
  });

  it('maps each screen id to its component', () => {
    expect(PANEL_COMPONENTS.datasets).toBe(DatasetsPanel);
    expect(PANEL_COMPONENTS['new-dataset']).toBe(NewDatasetPanel);
    expect(PANEL_COMPONENTS.version).toBe(VersionDetailPanel);
    expect(PANEL_COMPONENTS.recipes).toBe(RecipesPanel);
    expect(PANEL_COMPONENTS['label-runs']).toBe(LabelRunsPanel);
    expect(PANEL_COMPONENTS.calibration).toBe(CalibrationPanel);
    expect(PANEL_COMPONENTS.review).toBe(ReviewPanel);
    expect(PANEL_COMPONENTS['detector-sets']).toBe(DetectorSetsPanel);
    expect(PANEL_COMPONENTS.generation).toBe(GenerationPanel);
  });

  it.each([
    ['recipes', 'Import recipe'],
    ['version', 'Choose a version'],
    ['datasets', 'Your datasets (0)'],
    ['label-runs', 'No label runs yet. Start one from the Label step of a new dataset.'],
    ['calibration', 'No calibration records yet. Add a calibration set, then compute a record for a label run.'],
    ['review', 'No review queues yet. Create one, or draw an audit for a version.'],
    ['detector-sets', 'No detector sets yet. Create one from versions holding training rows, a test, out-of-distribution rows and calibration negatives.'],
    ['generation', 'No generation runs yet. Start one from a version with a held-out split.'],
  ])('the shell renders %s, not the empty state', async (id, marker) => {
    window.location.hash = `#/${id}`;
    render(<App />);
    expect(screen.queryByText('This screen is not built yet')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(marker).length).toBeGreaterThan(0));
  });

  it('the shell renders the guided flow with its step rail', async () => {
    window.location.hash = '#/new-dataset';
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('step-rail')).toBeInTheDocument());
    expect(screen.queryByText('This screen is not built yet')).not.toBeInTheDocument();
  });
});

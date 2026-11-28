// Guided flow (tasks 17.3, 17.4): the rail from /datasets/meta, discovered step panels, reported gaps.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useDraftsStore } from '@/stores/draftsStore';
import { mockFetch } from '@/test/fetchMock';
import { META } from '@/test/fixtures002';
import type { RecipeDraft } from '@/types/recipes';

import { GuidedStepHost } from './GuidedStepHost';
import { buildBlockers, stepStatuses } from './journey';
import { StepRail } from './StepRail';
import { discoveredStepPanels, stepsWithoutPanels } from './guidedSteps';

const DRAFT: RecipeDraft = { id: 'draft-1', recipe_id: null, dataset_id: null, name: null, body: {}, step_labels: [], inputs: [], flow_state: { step: 'goal', choices: {} }, updated_by: 'Ada', updated_at: '' };

afterEach(() => vi.unstubAllGlobals());

describe('StepRail', () => {
  it('marks done only what exists, and says why an earlier step is not done', () => {
    const onSelect = vi.fn();
    // Import and goal are done; profile was passed but has no version (novice walk finding 1).
    const draft = { ...DRAFT, dataset_id: 'd-1', inputs: [{ kind: 'source', source_id: 's-1' }] };
    render(<StepRail steps={META.guided_steps} current="curate" onSelect={onSelect} statuses={stepStatuses(META.guided_steps, draft)} />);
    const rail = screen.getByTestId('step-rail');
    expect(rail.querySelectorAll('li')).toHaveLength(7);
    expect(screen.getAllByText('done')).toHaveLength(2);
    expect(screen.getByText('not done: needs a built version')).toBeInTheDocument();
    expect(screen.getAllByText('current')).toHaveLength(1);
    expect(screen.getAllByText('to do')).toHaveLength(3);
    fireEvent.click(screen.getByText('label'));
    expect(onSelect).toHaveBeenCalledWith('label');
  });

  it('marks a built journey: profile and curate done once a version exists', () => {
    const draft = { ...DRAFT, dataset_id: 'd-1', inputs: [{ kind: 'source', source_id: 's-1' }], flow_state: { step: 'label', choices: { version_id: 'v-9' } } };
    const statuses = stepStatuses(META.guided_steps, draft);
    expect(Object.entries(statuses).filter(([, v]) => v.done).map(([k]) => k)).toEqual(['import', 'goal', 'profile', 'curate']);
  });
});

describe('buildBlockers', () => {
  const ok = { validation: { valid: true, body_errors: [], steps: [] }, validationCurrent: true, recipeName: 'r', building: false };
  it('names every missing piece, in the order to fix them', () => {
    expect(buildBlockers(DRAFT, { ...ok, recipeName: '' })).toEqual([
      'Choose a source on the Import step.',
      'Create the dataset on the Goal step.',
      'Add at least one step on the Curate step, for example "Sample rows".',
      'Name the recipe.',
    ]);
  });
  it('is empty for a ready draft with valid steps, and says so when they are invalid', () => {
    const ready = { ...DRAFT, dataset_id: 'd-1', inputs: [{ kind: 'source', source_id: 's-1' }], body: { format: 'dw.recipe/v1' as const, steps: [{ operator: 'sample', version: '1.0.0', params: { count: 25000 } }] } };
    expect(buildBlockers(ready, ok)).toEqual([]);
    expect(buildBlockers(ready, { ...ok, validation: { valid: false, body_errors: [], steps: [] } })).toEqual(['Fix the steps marked on the Curate step.']);
    expect(buildBlockers(ready, { ...ok, validationCurrent: false })).toEqual(['Checking the steps…']);
  });
});

describe('guided step panels', () => {
  it("discovers feature 002's Goal panel through the glob", () => {
    expect(discoveredStepPanels().map((p) => p.step)).toContain('goal');
  });

  it('reports every step no panel serves yet', () => {
    expect(stepsWithoutPanels(META.guided_steps, discoveredStepPanels())).not.toContain('goal');
    expect(stepsWithoutPanels(['goal', 'nobody'], discoveredStepPanels())).toEqual(['nobody']);
  });

  it('a step with no panel is reported as a fault, never as a feature the build lacks', () => {
    render(<GuidedStepHost step="label" draft={DRAFT} onAdvance={vi.fn()} panels={[]} />);
    expect(screen.getByText('The label step could not be shown')).toBeInTheDocument();
    expect(screen.getByText(/a fault in miDataworks/)).toBeInTheDocument();
    expect(screen.queryByText(/not built/)).not.toBeInTheDocument();
  });

  it('the Goal step creates the dataset and records it in the draft', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/datasets/meta': META,
      'POST /api/v1/datasets': { id: 'd-9', name: 'humor', target_type: 'dpo', version_list: [] },
      'GET /api/v1/datasets': { items: [], total: 0 },
    });
    useDraftsStore.setState({ draft: DRAFT });
    const onAdvance = vi.fn();
    render(<GuidedStepHost step="goal" draft={DRAFT} onAdvance={onAdvance} />);
    await waitFor(() => expect(screen.getByRole('option', { name: 'dpo' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Dataset name/), { target: { value: 'humor' } });
    fireEvent.change(screen.getByLabelText('Goal'), { target: { value: 'dpo' } });
    expect(screen.getByText('Rows are keyed on: prompt or chosen or rejected.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create the dataset' }));
    await waitFor(() => expect(onAdvance).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/datasets')?.body).toEqual({ name: 'humor', target_type: 'dpo' });
    expect(useDraftsStore.getState().draft?.dataset_id).toBe('d-9');
  });
});

describe('NewDatasetPanel commit points', () => {
  it('saves the draft as a recipe, builds from it, and shows progress', async () => {
    const { NewDatasetPanel } = await import('@/components/panels/NewDatasetPanel');
    const { useDatasetsStore } = await import('@/stores/datasetsStore');
    const { useVersionsStore } = await import('@/stores/versionsStore');
    const ready = { ...DRAFT, recipe_id: 'rec-1', dataset_id: 'd-1', body: { format: 'dw.recipe/v1', steps: [{ operator: 'stub_keep', version: '1', params: {} }] }, inputs: [{ kind: 'source', source_id: 's-1' }], flow_state: { step: 'assemble', choices: { dataset_name: 'humor' } } };
    window.localStorage.setItem('midataworks-guided-draft', JSON.stringify({ id: 'draft-1' }));
    const { calls } = mockFetch({
      'GET /api/v1/datasets/meta': META,
      'GET /api/v1/recipe-drafts/draft-1': ready,
      // the backend stores and returns what was sent (the build's job id lands in the choices)
      'PUT /api/v1/recipe-drafts/draft-1': (body: unknown) => ({ json: { ...ready, ...(body as object) } }),
      'POST /api/v1/recipes/validate': { valid: true, body_errors: [], steps: [{ index: 1, operator: 'stub_keep', version: '1', errors: [] }] },
      'POST /api/v1/recipe-drafts/draft-1/save': () => ({ status: 201, json: { id: 'rev-2', revision_number: 2 } }),
      'POST /api/v1/recipes/rec-1/build': () => ({ status: 202, json: { job_id: 'job_7', existing_job: false, seed: 4, request_digest: 'x' } }),
    });
    useDatasetsStore.setState({ meta: null });
    useVersionsStore.setState({ build: null });
    const panel = { id: 'new-dataset', label: 'New dataset', title: 'New dataset', subtitle: 'Seven steps.', icon: () => null, feature: '002' };
    render(<NewDatasetPanel panel={panel as never} />);
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: 'New dataset humor' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Open Generation' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save draft as recipe' }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/save'))).toBe(true));
    // enabled once the registry has answered for these steps (finding 1: never disabled silently)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Build version' })).toBeEnabled());
    expect(screen.queryByTestId('build-blockers')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Build version' }));
    await waitFor(() => expect(screen.getByTestId('build-progress')).toHaveTextContent('Building: queued, 0%'));
    const sent = calls.find((c) => c.path === '/api/v1/recipes/rec-1/build');
    expect(sent?.body).toEqual({ dataset_id: 'd-1', inputs: [{ kind: 'source', source_id: 's-1' }] });
    act(() => useVersionsStore.getState().applyBuildEvent('version_build:completed', { job_id: 'job_7', version_id: 'v-9' }));
    expect(screen.getByRole('button', { name: 'Open the version' })).toBeInTheDocument();
    // the built version becomes the journey's version: Profile, Label and Export read it
    await waitFor(() => expect((useDraftsStore.getState().draft?.flow_state.choices as { version_id?: string }).version_id).toBe('v-9'));
  });
});

describe('every guided step the backend names has a panel', () => {
  it('serves every step the backend names, derived from the meta fixture', () => {
    // The backend's GUIDED_STEPS (GET /datasets/meta) is the authority; META.guided_steps is pinned
    // to it by backend/tests/unit/test_guided_steps_fixture.py, so a step added there without a
    // panel turns this red. The export step had no panel until 2026-10-07.
    expect(META.guided_steps.length).toBeGreaterThanOrEqual(7);
    expect(stepsWithoutPanels(META.guided_steps, discoveredStepPanels())).toEqual([]);
  });
});

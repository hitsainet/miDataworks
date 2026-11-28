// ENH-011 #1, the guided-draft half (DW-1 tasks 1.1–1.2; 021 FTID I16's rule). The Curate step rebuilt
// the draft's body from its steps alone, and `updateDraft` replaces `body` whole, so a draft whose body
// declared `visibility_floor` or `sensitivity` lost both on the first step edit; the recipe saved from
// it no longer floored its versions. These tests run the real store and API client over a fetch stub
// and assert the payload that reaches the drafts API.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { discoveredStepPanels } from '@/components/guided/guidedSteps';
import { resetOperatorChoices } from '@/components/recipes/operatorChoices';
import { useDraftsStore } from '@/stores/draftsStore';
import { mockFetch } from '@/test/fetchMock';
import { CURATE_LIST, SAMPLE_OP } from '@/test/fixtures003';
import type { DraftWrite, RecipeDraft } from '@/types/recipes';

const STEP = { operator: 'sample', version: '1.0.0', params: { count: 10 } };
const draftWith = (body: Record<string, unknown>): RecipeDraft => ({
  id: 'draft-1', recipe_id: 'rec-1', dataset_id: 'd-1', name: null,
  body: body as RecipeDraft['body'], step_labels: [], inputs: [],
  flow_state: { step: 'curate', choices: {} }, updated_by: 'Ada', updated_at: '',
});
const FLOORED = draftWith({ format: 'dw.recipe/v1', steps: [STEP], visibility_floor: 'private_only', sensitivity: 'sensitive' });

const curateStep = () => discoveredStepPanels().find((p) => p.step === 'curate')!.Component;

/** Renders the Curate step over `draft`, recording every patch the step hands `updateDraft`. */
function renderStep(draft: RecipeDraft) {
  const { calls } = mockFetch({
    'GET /api/v1/operators': CURATE_LIST,
    'GET /api/v1/operators/sample/1.0.0': SAMPLE_OP,
    'POST /api/v1/recipes/validate': { valid: true, body_errors: [], steps: [] },
    'PUT /api/v1/recipe-drafts/draft-1': (body: unknown) => ({ json: { ...draft, ...(body as object) } }),
  });
  useDraftsStore.setState({ draft });
  const real = useDraftsStore.getState().updateDraft;
  const patches: Array<Partial<DraftWrite>> = [];
  useDraftsStore.setState({ updateDraft: (patch) => { patches.push(patch); real(patch); } });
  const Step = curateStep();
  render(<Step draft={draft} onAdvance={vi.fn()} />);
  const puts = () => calls.filter((c) => c.method === 'PUT' && c.path === '/api/v1/recipe-drafts/draft-1');
  return { patches, puts };
}

beforeEach(() => resetOperatorChoices());
afterEach(() => {
  vi.unstubAllGlobals();
  useDraftsStore.setState({ draft: null, validation: null, validatedKey: null, error: null });
});

describe('the guided Curate step keeps recipe-level declarations (ENH-011 #1)', () => {
  it('an edited step and an added step both keep visibility_floor and sensitivity, through to the drafts API', async () => {
    const { patches, puts } = renderStep(FLOORED);
    const count = await screen.findByLabelText(/Rows to keep/);
    fireEvent.change(count, { target: { value: '500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add another step' }));

    // Two edits, two patches; each carries both declarations beside the edited steps.
    expect(patches).toHaveLength(2);
    expect(patches[0].body).toEqual({
      format: 'dw.recipe/v1', steps: [{ ...STEP, params: { count: 500 } }], visibility_floor: 'private_only', sensitivity: 'sensitive',
    });
    expect(patches[1].body).toEqual({
      format: 'dw.recipe/v1',
      steps: [{ ...STEP, params: { count: 500 } }, { operator: '', version: '', params: {} }],
      visibility_floor: 'private_only',
      sensitivity: 'sensitive',
    });

    // The debounced save sends one PUT, whose body is the draft's whole body, declarations included.
    await act(() => useDraftsStore.getState().flushDraft());
    expect(puts()).toHaveLength(1);
    const sent = puts()[0].body as Partial<DraftWrite>;
    expect(sent.recipe_id).toBe('rec-1');
    expect(sent.body).toEqual(patches[1].body);
  });

  it('carries every recipe-level key the body holds, not a named list, and omits an absent one', async () => {
    // A future declaration must survive without another edit here (021 FTID I16); a null is "absent".
    const draft = draftWith({ format: 'dw.recipe/v1', steps: [STEP], visibility_floor: 'private_only', sensitivity: null, a_later_declaration: 'kept' });
    const { patches, puts } = renderStep(draft);
    fireEvent.change(await screen.findByLabelText(/Rows to keep/), { target: { value: '7' } });
    expect(patches).toHaveLength(1);
    expect(patches[0].body).toEqual({
      format: 'dw.recipe/v1', steps: [{ ...STEP, params: { count: 7 } }], visibility_floor: 'private_only', a_later_declaration: 'kept',
    });
    await act(() => useDraftsStore.getState().flushDraft());
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect((puts()[0].body as Partial<DraftWrite>).body).toEqual(patches[0].body);
  });

  it('a draft with no declarations gains none', async () => {
    const { patches } = renderStep(draftWith({ format: 'dw.recipe/v1', steps: [STEP] }));
    fireEvent.change(await screen.findByLabelText(/Rows to keep/), { target: { value: '3' } });
    expect(patches[0].body).toEqual({ format: 'dw.recipe/v1', steps: [{ ...STEP, params: { count: 3 } }] });
  });
});

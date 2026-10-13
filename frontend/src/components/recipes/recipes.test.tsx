// Recipes screen parts (tasks 14.3–14.5).
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RECIPE, SUMMARY } from '@/test/fixtures002';

import { RecipeCard } from './RecipeCard';
import { RecipeEditor, toBody, toEditable } from './RecipeEditor';

describe('RecipeCard', () => {
  it('shows name, short hash, steps, providers, revisions and versions built', () => {
    const actions = { onEdit: vi.fn(), onClone: vi.fn(), onExport: vi.fn(), onArchive: vi.fn(), onBuild: vi.fn() };
    render(<RecipeCard recipe={SUMMARY} actions={actions} />);
    const card = screen.getByTestId('recipe-card');
    for (const text of ['humor-curation', '2 steps', 'native', '3 revisions', '4 versions built']) expect(card).toHaveTextContent(text);
    expect(screen.getByTitle('b'.repeat(64))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Export recipe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Build version' }));
    expect(actions.onExport).toHaveBeenCalledWith(SUMMARY);
    expect(actions.onBuild).toHaveBeenCalledTimes(1);
  });
});

describe('RecipeEditor', () => {
  it('round-trips the head revision into editable steps and back', () => {
    const steps = toEditable(RECIPE);
    expect(steps[0]).toEqual({ operator: 'stub_drop_short', version: '1', params: '{"min_len":8}', label: 'short' });
    expect(toBody(steps).body).toEqual(RECIPE.revisions[0].body);
    expect(toBody(steps).labels).toEqual(['short', 'all']);
  });

  it('adds, reorders with the keyboard buttons, removes, and shows per-step errors', () => {
    const onSave = vi.fn();
    const validation = { valid: false, body_errors: [], steps: [{ index: 2, operator: 'stub_keep', version: '1', errors: [{ code: 'operator_not_allowed', message: 'stub_keep 1 is not allowlisted.' }] }] };
    render(<RecipeEditor recipe={RECIPE} validation={validation} onValidate={vi.fn()} onSave={onSave} onCancel={vi.fn()} />);
    expect(screen.getAllByTestId('step-row')).toHaveLength(2);
    expect(screen.getAllByTestId('step-row')[1]).toHaveTextContent('stub_keep 1 is not allowlisted.');
    fireEvent.click(screen.getByRole('button', { name: 'Move step 2 up' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add step' }));
    expect(screen.getAllByTestId('step-row')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Remove step 3' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save revision' }));
    expect(onSave.mock.calls[0][1].steps.map((s: { operator: string }) => s.operator)).toEqual(['stub_keep', 'stub_drop_short']);
  });

  it('refuses to save parameters that are not JSON, saying so beside the step', () => {
    render(<RecipeEditor recipe={RECIPE} validation={null} onValidate={vi.fn()} onSave={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(screen.getAllByLabelText('Parameters (JSON)')[0], { target: { value: '{min_len: 8' } });
    expect(screen.getByText('The parameters are not valid JSON.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save revision' })).toBeDisabled();
  });
});

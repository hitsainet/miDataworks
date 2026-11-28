// Feature 016's Rule signals screen (FR-016.40): registered in BOTH registries and rendered by the
// shell; read-only; the preview sends nothing until the operator clicks Preview.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { RuleSignalsPanel } from '@/components/rules/RuleSignalsPanel';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { PANELS } from '@/config/panels';
import { useRuleSignalsStore } from '@/stores/ruleSignalsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { META } from '@/test/fixtures002';

const RULE_SET = {
  name: 'words-rules',
  behaviour_key: 'key-one',
  version: 2,
  sha256: 'a'.repeat(64),
  counts: { signals: 2, outputs: 2, vetoes: 0, cases: 3 },
  last_validation: { source: 'save', at: '2026-10-23T18:00:00+00:00', passed: true, cases: 3 },
  archived: false,
};
const DETAIL = {
  name: 'words-rules',
  version: 2,
  behaviour_key: 'key-one',
  sha256: 'a'.repeat(64),
  archived: false,
  created_at: '2026-10-23T18:00:00+00:00',
  superseded_by: null,
  last_validation: RULE_SET.last_validation,
  used_by: [{ version_id: 'v'.repeat(36), dataset_id: 'd', number: 4, step_index: 2, use: 'screen' }],
  next: { tool: 'dataworks_preview_rule_set', args: {}, why: '' },
  document: {
    name: 'words-rules',
    behaviour_key: 'key-one',
    description: 'Two invented words.',
    signals: [
      { id: 'q', kind: 'pattern' },
      { id: 'near', kind: 'proximity', unit: 'utf8_bytes', max_distance: 12 },
    ],
    expressions: {},
    outputs: [{ column: 'hit', type: 'bool', value: { fired: 'any_word' } }],
    cases: [{ id: 'one', input: { text: 'x' }, expect: {}, note: 'fires' }],
  },
};
const PREVIEW = {
  sha256: 'a'.repeat(64),
  spans_unit: 'code_points',
  signals: { q: { fired: true, spans: [[2, 5]], substrings: ['abc'], outputs: [] } },
  vetoed: false,
  outputs: { hit: true },
  deciding: { hit: { expression: 'any_word', fired: true, vetoes: [] } },
};

const derivation = (state: string, figures: Record<string, number | null>) => ({
  id: 'rdv_0123456789abcdef01234567',
  job_id: 'job_1',
  source_version_id: 'v1',
  status: 'completed',
  state,
  completed_at: '2026-10-23T18:00:00+00:00',
  result: {
    draft: figures,
    signals: { d1: figures },
    literals: [{ literal: 'zorbligate', support: 20, precision: 1 }],
    held_out_per_class: { positive: 3, negative: 4 },
    seed: 3,
  },
});
const NONE = { precision: null, recall: null, coverage: null, fired: null };

const BASE = {
  'GET /api/health': HEALTH,
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/datasets/meta': META,
  'GET /api/v1/rule-sets': { items: [RULE_SET], total: 1 },
  'GET /api/v1/rule-sets/words-rules': DETAIL,
  'GET /api/v1/validators': { items: [], total: 0 },
  'POST /api/v1/rule-sets/preview': PREVIEW,
};

describe('Rule signals screen (016)', () => {
  let calls: Array<{ method: string; path: string; body: unknown }>;
  beforeEach(() => {
    calls = mockFetch(BASE).calls;
    // The store outlives a test; a rule set left open would make the next click close it.
    useRuleSignalsStore.setState({ current: null, validator: null, preview: null });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = '';
  });

  it('is in the panel registry and the component map, after detector sets', () => {
    const ids = PANELS.map((p) => p.id);
    expect(ids).toContain('rule-signals');
    expect(ids.indexOf('rule-signals')).toBe(ids.indexOf('detector-sets') + 1);
    expect(PANEL_COMPONENTS['rule-signals']).toBe(RuleSignalsPanel);
  });

  it('the shell renders it from the registry, with the list and a detail', async () => {
    window.location.hash = '#/rule-signals';
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('rule-sets-table')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'words-rules' }));
    // the screen's Help names a real topic, whose Manual link opens the rule-signals page
    expect(screen.getByTestId('manual-link-rule-signals')).toHaveAttribute('href', expect.stringContaining('guide/rule-signals'));
    const detail = await screen.findByTestId('rule-set-detail');
    expect(detail).toHaveTextContent('proximity within 12 bytes (UTF-8)');
    expect(detail).toHaveTextContent('version 4');
    expect(detail).toHaveTextContent('ET');
  });

  it('the behaviour filter and archived switch reach the list request', async () => {
    window.location.hash = '#/rule-signals';
    render(<App />);
    await screen.findByTestId('rule-sets-table');
    fireEvent.change(screen.getByPlaceholderText('all behaviours'), { target: { value: 'key-one' } });
    fireEvent.click(screen.getByLabelText('Show archived rule sets'));
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes('behaviour_key=key-one') && c.path.includes('include_archived=true'))).toBe(true),
    );
  });

  it('the preview sends nothing while typing and exactly one request on click', async () => {
    window.location.hash = '#/rule-signals';
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'words-rules' }));
    await screen.findByTestId('rule-set-detail');
    fireEvent.click(screen.getByRole('tab', { name: 'Preview' }));
    const box = screen.getByLabelText('Text to preview');
    fireEvent.change(box, { target: { value: 'a text with abc' } });
    fireEvent.change(box, { target: { value: 'a text with abc and more' } });
    const previews = () => calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/rule-sets/preview');
    expect(previews()).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByTestId('preview-result');
    expect(previews()).toHaveLength(1);
    expect(previews()[0].body).toEqual({ name: 'words-rules', version: 2, text: 'a text with abc and more' });
    expect(screen.getByTestId('preview-result')).toHaveTextContent('Spans, in characters (code points)');
  });


  it.each([
    ['not_measured', NONE, 'Not measured'],
    ['low_coverage', { precision: 0.5, recall: 0.25, coverage: 0.1, fired: 4 }, 'Low coverage'],
  ])('a %s derivation reads in words; "Not measured" is never a number, a check or green', async (state, figures, word) => {
    mockFetch({ ...BASE, 'GET /api/v1/rule-derivations/rdv_0123456789abcdef01234567': derivation(state, figures) });
    window.location.hash = '#/rule-signals';
    render(<App />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Derivations' }));
    fireEvent.change(screen.getByLabelText('Derivation id'), { target: { value: 'rdv_0123456789abcdef01234567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    const detail = await screen.findByTestId('derivation-detail');
    expect(detail).toHaveTextContent(word);
    if (state === 'not_measured') {
      expect(detail.querySelectorAll('td')[1]).toHaveTextContent(/^Not measured$/);
      const cells = Array.from(detail.querySelectorAll('td')).map((c) => c.textContent ?? '');
      expect(cells.some((c) => /\d+(\.\d+)?%/.test(c))).toBe(false);
      expect(detail.textContent).not.toMatch(/[✓✔]/);
      expect(detail.innerHTML).not.toMatch(/green|emerald/);
    } else {
      expect(detail).toHaveTextContent('50.0%');
    }
  });

  // Operator, 2026-10-31: "ensure the panel opens directly below the ruleset in the list rather than
  // below the entire list, and clicking the ruleset link again closes the panel ... the same steps on
  // the list and list items (links) on the 'Validator' tab as well."
  describe('a detail opens under its own row and closes by its link, the X and Escape', () => {
    const OTHER = { ...RULE_SET, name: 'other-rules' };
    const VALIDATOR = (id: string) => ({
      id, version: 1, ref: `${id}@1`, description: `Checks ${id}.`, reasons: ['bad_digit'], outputs: [],
      superseded_by: null, valid_path: null, case_count: 1,
    });
    beforeEach(() => {
      mockFetch({
        ...BASE,
        'GET /api/v1/rule-sets': { items: [RULE_SET, OTHER], total: 2 },
        'GET /api/v1/validators': { items: [VALIDATOR('luhn'), VALIDATOR('iban')], total: 2 },
        'GET /api/v1/validators/luhn/versions/1': { ...VALIDATOR('luhn'), cases: [{ id: 'c1', input: {}, expect: { reason: 'bad_digit' } }] },
      });
    });

    /** The table rows in order, each named by its first cell or its detail. */
    const rowOrder = (table: HTMLElement) =>
      Array.from(table.querySelectorAll('tbody > tr')).map((tr) =>
        tr.querySelector('[data-testid$="-detail"]')?.getAttribute('data-testid') ?? (tr.querySelector('td')?.textContent ?? '').trim(),
      );

    it('Rule sets: the detail sits between its row and the next; the link toggles it', async () => {
      window.location.hash = '#/rule-signals';
      render(<App />);
      const table = await screen.findByTestId('rule-sets-table');
      const link = screen.getByRole('button', { name: 'words-rules' });
      expect(link).toHaveAttribute('aria-expanded', 'false');
      fireEvent.click(link);
      await screen.findByTestId('rule-set-detail');
      expect(rowOrder(table)).toEqual(['words-rules', 'rule-set-detail', 'other-rules']);
      expect(link).toHaveAttribute('aria-expanded', 'true');
      fireEvent.click(link);
      expect(screen.queryByTestId('rule-set-detail')).toBeNull();
      expect(link).toHaveAttribute('aria-expanded', 'false');
      // the X closes it
      fireEvent.click(link);
      await screen.findByTestId('rule-set-detail');
      fireEvent.click(screen.getByRole('button', { name: 'Close' }));
      expect(screen.queryByTestId('rule-set-detail')).toBeNull();
      // Escape closes it
      fireEvent.click(link);
      await screen.findByTestId('rule-set-detail');
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(screen.queryByTestId('rule-set-detail')).toBeNull();
    });

    it('Validators: the detail sits between its row and the next; the link toggles it', async () => {
      window.location.hash = '#/rule-signals';
      render(<App />);
      fireEvent.click(await screen.findByRole('tab', { name: 'Validators' }));
      const link = await screen.findByRole('button', { name: 'luhn@1' });
      const table = screen.getByTestId('validators-table');
      expect(link).toHaveAttribute('aria-expanded', 'false');
      fireEvent.click(link);
      await screen.findByTestId('validator-detail');
      expect(rowOrder(table)).toEqual(['luhn@1', 'validator-detail', 'iban@1']);
      expect(link).toHaveAttribute('aria-expanded', 'true');
      fireEvent.click(link);
      expect(screen.queryByTestId('validator-detail')).toBeNull();
      expect(link).toHaveAttribute('aria-expanded', 'false');
      fireEvent.click(link);
      await screen.findByTestId('validator-detail');
      fireEvent.click(screen.getByRole('button', { name: 'Close' }));
      expect(screen.queryByTestId('validator-detail')).toBeNull();
      fireEvent.click(link);
      await screen.findByTestId('validator-detail');
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(screen.queryByTestId('validator-detail')).toBeNull();
    });
  });
});

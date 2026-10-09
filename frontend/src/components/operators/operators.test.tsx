// Feature 003's screen parts (FTASKS 11.2-11.9).
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { getPanel } from '@/config/panels';
import { useOperatorsStore } from '@/stores/operatorsStore';
import { HEALTH, mockFetch } from '@/test/fetchMock';
import { ALLOWLIST, DROP_SHORT, LIST, PREVIEW, STATS } from '@/test/fixtures003';
import SUBSET from '@/test/schemaSubset.json';
import type { JsonSchemaProperty, ParamsSchema } from '@/types/operators';

import { AllowlistSection } from './AllowlistSection';
import { OperatorCard } from './OperatorCard';
import { OperatorsPanel } from './OperatorsPanel';
import { thresholdGroups } from './OperatorDetail';
import { PreviewPanel } from './PreviewPanel';
import { RENDERED_KEYWORDS, RENDERED_TYPES, SchemaForm } from './SchemaForm';
import { histogram, ThresholdControl, wouldDrop } from './ThresholdControl';

// Recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks. Set once, not with
// vi.stubGlobal, because the fetch stubs are cleared with vi.unstubAllGlobals().
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const ROUTES = {
  'GET /api/health': HEALTH,
  'GET /api/v1/approvals': { approvals: [] },
  'GET /api/v1/jobs': { jobs: [] },
  'GET /api/v1/operators': LIST,
  'GET /api/v1/operators/schema-subset': SUBSET,
  'GET /api/v1/operators/allowlist': ALLOWLIST,
  'GET /api/v1/operators/fx_drop_short/1': DROP_SHORT,
};

const reset = () => useOperatorsStore.setState({ catalogue: [], filters: {}, previews: {}, statistics: {}, fieldErrors: [], selected: null, subset: null, allowlist: [], catalogueError: null, catalogueLoading: false });

describe('SchemaForm', () => {
  it('handles every keyword and type the backend subset lists', () => {
    const listed = new Set([...SUBSET.top_level, ...SUBSET.common, ...Object.values(SUBSET.by_type).flat(), ...SUBSET.items, ...SUBSET.extensions]);
    const missing = [...listed].filter((k) => !RENDERED_KEYWORDS.has(k));
    expect(missing, 'a subset keyword has no renderer').toEqual([]);
    expect(SUBSET.property_types.filter((t) => !RENDERED_TYPES.has(t))).toEqual([]);
  });

  it('renders each field type with title, unit, hint, constraint and default', () => {
    const schema: ParamsSchema = {
      type: 'object',
      additionalProperties: false,
      required: ['n'],
      properties: {
        n: { type: 'integer', minimum: 1, maximum: 9, 'x-unit': 'words', 'x-hint': 'How many.', default: 3, title: 'Count' },
        s: { type: 'string', 'x-widget': 'textarea', maxLength: 20 },
        b: { type: 'boolean' },
        e: { enum: ['x', 'y'] },
        a: { type: 'array', items: { enum: ['p', 'q'] } },
        l: { type: 'array', items: { type: 'number' } },
        c: { const: 7 },
        adv: { type: 'number', 'x-advanced': true },
      },
    };
    render(<SchemaForm schema={schema} values={{}} onChange={vi.fn()} />);
    const count = screen.getByTestId('field-n');
    for (const text of ['Count', '(words)', 'How many.', 'at least 1', 'at most 9', 'Default: 3']) expect(count).toHaveTextContent(text);
    expect(screen.getByLabelText('required')).toBeInTheDocument();
    expect(screen.getByTestId('field-s').querySelector('textarea')).not.toBeNull();
    expect(screen.getByTestId('field-a')).toHaveTextContent('p');
    expect(screen.getByText('Advanced')).toBeInTheDocument();
    expect(screen.getByTestId('field-c')).toHaveTextContent('7');
  });

  it('shows the server error for min_len="abc" beside its field and keeps what was typed', () => {
    const onChange = vi.fn();
    render(
      <SchemaForm
        schema={DROP_SHORT.manifest!.params_schema}
        values={{ min_len: 'abc' }}
        onChange={onChange}
        errors={[{ pointer: '/min_len', message: "'abc' is not of type 'integer'" }]}
      />,
    );
    expect(screen.getByTestId('field-error-min_len')).toHaveTextContent("'abc' is not of type 'integer'");
    fireEvent.change(screen.getByLabelText(/Minimum length/), { target: { value: '12' } });
    expect(onChange).toHaveBeenCalledWith({ min_len: 12 });
    fireEvent.change(screen.getByLabelText(/Minimum length/), { target: { value: 'x1' } });
    expect(onChange).toHaveBeenLastCalledWith({ min_len: 'x1' });
  });

  it('never renders an unknown keyword silently', () => {
    const schema: ParamsSchema = {
      type: 'object',
      additionalProperties: false,
      properties: { weird: { type: 'string', format: 'email' } as JsonSchemaProperty },
    };
    render(<SchemaForm schema={schema} values={{}} />);
    expect(screen.getByTestId('field-unsupported-weird')).toHaveTextContent('format');
  });
});

describe('ThresholdControl', () => {
  it('counts drops on the statistic scale and names the sample', () => {
    render(<ThresholdControl thresholds={STATS.thresholds} sampleSize={10} />);
    expect(screen.getByTestId('drop-count')).toHaveTextContent('Drops 2 of 10 sample rows (20.0%)');
    fireEvent.change(screen.getByLabelText('min_len cutoff'), { target: { value: '30' } });
    expect(screen.getByTestId('drop-count')).toHaveTextContent('Drops 5 of 10 sample rows (50.0%)');
    expect(screen.getByTestId('drop-excerpts').children).toHaveLength(5);
  });

  it('many slider moves make no request at all', async () => {
    const { calls } = mockFetch({});
    render(<ThresholdControl thresholds={STATS.thresholds} sampleSize={10} />);
    for (const v of [4, 10, 20, 33, 50, 61, 80, 3]) fireEvent.change(screen.getByLabelText('min_len cutoff'), { target: { value: String(v) } });
    expect(calls).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it('a band draws two cutoffs and drops outside both', () => {
    const base = STATS.thresholds[0];
    const band = [
      { ...base, param: 'min_len', pair_param: 'max_len', current: 10 },
      { ...base, param: 'max_len', drop_when: 'above' as const, pair_param: 'min_len', current: 70 },
    ];
    expect(thresholdGroups(band)).toHaveLength(1);
    render(<ThresholdControl thresholds={band} sampleSize={10} />);
    expect(screen.getByLabelText('max_len cutoff')).toBeInTheDocument();
    expect(screen.getByTestId('drop-count')).toHaveTextContent('Drops 4 of 10');
  });

  it('a constant statistic says the sample cannot inform the cut', () => {
    const constant = [{ ...STATS.thresholds[0], constant: true, values: STATS.thresholds[0].values.map((v) => ({ ...v, value: 5 })) }];
    render(<ThresholdControl thresholds={constant} sampleSize={10} />);
    expect(screen.getByTestId('constant-statistic')).toHaveTextContent('cannot inform this cut');
    expect(histogram([5, 5, 5])).toEqual([{ mid: 5, count: 3 }]);
  });

  it('commits the cut through onCommit', () => {
    const onCommit = vi.fn();
    render(<ThresholdControl thresholds={STATS.thresholds} sampleSize={10} onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText('min_len cutoff'), { target: { value: '19' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use this cut' }));
    expect(onCommit).toHaveBeenCalledWith({ min_len: 19 });
    expect(wouldDrop(null, [{ param: 'x', drop_when: 'below', value: 1 }])).toBe(false);
  });
});

describe('PreviewPanel and cards', () => {
  it('shows each drop with its reason and statistic in mono type', () => {
    render(<PreviewPanel result={PREVIEW} />);
    expect(screen.getByTestId('preview-dropped')).toHaveTextContent('too_short: text has 3 characters, below 10');
    expect(screen.getByTestId('preview-dropped')).toHaveTextContent('text_length=3');
    fireEvent.click(screen.getByRole('tab', { name: /Kept/ }));
    expect(screen.getByTestId('preview-kept')).toHaveTextContent('a long enough row');
  });

  it('an empty sample says so', () => {
    render(<PreviewPanel result={{ ...PREVIEW, empty: true, message: 'No rows to preview.' }} />);
    expect(screen.getByText('No rows to preview.')).toBeInTheDocument();
  });

  it('a not-allowed card says what to do next', () => {
    render(<OperatorCard entry={LIST.items[2]} onOpen={vi.fn()} />);
    expect(screen.getByTestId('operator-card')).toHaveTextContent('Not allowed');
    expect(screen.getByTestId('operator-card')).toHaveTextContent('Allow its entry point');
  });

  it('allowlist buttons name their action and require a reason', async () => {
    const onChange = vi.fn(async () => true);
    render(<AllowlistSection items={ALLOWLIST.items} error={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Allow tagger' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Write a reason');
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'reviewed' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Allow tagger' })));
    expect(onChange).toHaveBeenCalledWith('allow', ALLOWLIST.items[0], 'reviewed');
  });
});

describe('OperatorsPanel', () => {
  beforeEach(reset);
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = '';
  });

  it('is the registered operators screen and the shell renders it', async () => {
    mockFetch(ROUTES);
    expect(PANEL_COMPONENTS.operators).toBe(OperatorsPanel);
    window.location.hash = '#/operators';
    render(<App />);
    await waitFor(() => expect(screen.getAllByTestId('operator-card')).toHaveLength(3));
    expect(screen.queryByText('This screen is not built yet')).not.toBeInTheDocument();
  });

  it('opens a detail, checks settings, previews and draws the statistic once', async () => {
    const { calls } = mockFetch({
      ...ROUTES,
      'POST /api/v1/operators/fx_drop_short/1/validate': () => ({
        status: 422,
        json: { error: { code: 'params_invalid', message: 'bad', details: { errors: [{ pointer: '/min_len', message: "'abc' is not of type 'integer'" }] } } },
      }),
      'POST /api/v1/operators/fx_drop_short/1/preview': { status: 'done', preview_id: 'p', result: PREVIEW },
      'POST /api/v1/operators/fx_drop_short/1/statistics': { status: 'done', preview_id: 's', result: STATS },
    });
    render(<OperatorsPanel panel={getPanel('operators')} />);
    await waitFor(() => expect(screen.getAllByTestId('operator-card')).toHaveLength(3));
    expect(screen.getByTestId('operator-summary')).toHaveTextContent('2 allowed · 1 not allowed');
    fireEvent.click(screen.getAllByTestId('operator-card')[0]);
    await waitFor(() => expect(screen.getByTestId('operator-detail')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Minimum length/), { target: { value: 'abc' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Check settings' })));
    expect(screen.getByTestId('field-error-min_len')).toHaveTextContent('not of type');
    fireEvent.change(screen.getByLabelText('Version ID to sample from'), { target: { value: 'v1' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Preview on a sample' })));
    await waitFor(() => expect(screen.getByTestId('preview-panel')).toBeInTheDocument());
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Show the statistic' })));
    await waitFor(() => expect(screen.getByTestId('threshold-control')).toBeInTheDocument());
    for (const v of ['20', '40', '60']) fireEvent.change(screen.getByLabelText('min_len cutoff'), { target: { value: v } });
    expect(calls.filter((c) => c.path.endsWith('/statistics'))).toHaveLength(1);
  });

  it('shows the error and empty states', async () => {
    mockFetch({ ...ROUTES, 'GET /api/v1/operators': () => ({ status: 503, json: { error: { code: 'U', message: 'Down.', details: {} } } }) });
    const first = render(<OperatorsPanel panel={getPanel('operators')} />);
    await waitFor(() => expect(screen.getByTestId('operators-error')).toHaveTextContent('Down.'));
    first.unmount();
    vi.unstubAllGlobals();
    reset();
    mockFetch({ ...ROUTES, 'GET /api/v1/operators': { ...LIST, items: [], total: 0 } });
    render(<OperatorsPanel panel={getPanel('operators')} />);
    await waitFor(() => expect(screen.getByTestId('operators-empty')).toBeInTheDocument());
  });

  it('opens the package guide instead of installing anything', async () => {
    mockFetch(ROUTES);
    render(<OperatorsPanel panel={getPanel('operators')} />);
    fireEvent.click(screen.getByRole('button', { name: 'How to add an operator package' }));
    expect(screen.getByTestId('add-package-guide')).toHaveTextContent('Nothing is installed while the app runs');
  });
});

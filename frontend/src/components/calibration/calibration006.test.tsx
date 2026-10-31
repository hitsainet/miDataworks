// Feature 006 calibration screen parts (FTASKS 12.3, 12.4, 12.9).
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CalibrationPanel, latestPerLabeler } from '@/components/panels/CalibrationPanel';
import { getPanel } from '@/config/panels';
import { useCalibrationStore } from '@/stores/calibrationStore';
import { mockFetch } from '@/test/fetchMock';
import { QH, record } from '@/test/fixtures006';

import { AddCalibrationSetDialog, PREVIEW_DEBOUNCE_MS } from './AddCalibrationSetDialog';
import { formatInterval, statusWords, verdictText } from './format';
import { EMPTY_DRAFT, toMapping } from './MappingForm';
import { RecordCard } from './RecordCard';

// Recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks (as in operators.test.tsx).
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  useCalibrationStore.setState({ records: [], sets: [], targets: {}, jobs: {}, preview: null, previewError: null, error: null });
});

describe('verdictText (one test per rule)', () => {
  it('held-out rater, met', () => {
    expect(verdictText({ verdict: 'passes', rule: 'held_out_rater', numbers: { compared: 0.755, threshold: 0.731 } })).toBe('Passes · level with held-out rater (0.755 ≥ 0.731)');
  });
  it('default C3, met', () => {
    expect(verdictText({ verdict: 'passes', rule: 'default_c3', numbers: { compared: 0.742, threshold: 0.7 } })).toBe('Passes · CI lower bound 0.742 ≥ 0.70 (default)');
  });
  it('default C3, missed', () => {
    expect(verdictText({ verdict: 'fails', rule: 'default_c3', numbers: { compared: 0.66, threshold: 0.7 } })).toBe('Fails · CI lower bound 0.660 < 0.70 (default)');
  });
  it('operator target', () => {
    expect(verdictText({ verdict: 'passes', rule: 'operator_target', numbers: { compared: 0.8, threshold: 0.8 } })).toBe('Passes · CI lower bound 0.800 ≥ 0.800 (operator target)');
  });
  it('invalid and insufficient', () => {
    expect(verdictText({ verdict: 'invalid', rule: null, numbers: {} })).toBe('Invalid · a sanity check failed');
    expect(verdictText({ verdict: 'insufficient', rule: null, numbers: {} })).toContain('both human classes');
  });
  it('formats an interval with three decimals', () => {
    expect(formatInterval({ value: 0.7533, ci_low: 0.7416, ci_high: 0.7654 })).toBe('0.753 [0.742, 0.765]');
  });
  it('reads a status: invalid and insufficient are never calibrated', () => {
    const base = { status: 'recorded' as const, record_id: 'cr', rule: null, auroc: null, calibration_set: null };
    expect(statusWords({ ...base, verdict: 'invalid' }).tone).toBe('fail');
    expect(statusWords({ ...base, verdict: 'insufficient' }).tone).toBe('fail');
    expect(statusWords({ ...base, verdict: 'passes' }).tone).toBe('pass');
    expect(statusWords(undefined).tone).toBe('none');
  });
});

describe('RecordCard', () => {
  it('shows four tiles, each number with its sample, and the pill in words', () => {
    render(<RecordCard record={record()} />);
    expect(screen.getByTestId('tile-auroc')).toHaveTextContent('0.753');
    expect(screen.getByTestId('tile-auroc')).toHaveTextContent('[0.742, 0.765] · 6,600 rows');
    expect(screen.getByTestId('tile-ceiling')).toHaveTextContent('20 draws');
    expect(screen.getByTestId('tile-paired')).toHaveTextContent('945 pairs, 745 groups');
    expect(screen.getByTestId('tile-confident')).toHaveTextContent('6,600 rows');
    expect(screen.getByTestId('verdict-pill')).toHaveTextContent('Passes · level with held-out rater (0.756 ≥ 0.733)');
    expect(screen.getByTestId('reference-diagnostic')).toHaveTextContent('Does not measure the concept');
  });

  it('renders "Not available" reasons instead of numbers', () => {
    const r = record();
    r.metrics = { ...r.metrics, ceiling: null, paired: null, reasons: { ceiling: 'Not available: no per-rater column', paired: 'Not available: no group column' } };
    render(<RecordCard record={r} />);
    expect(screen.getByTestId('tile-ceiling')).toHaveTextContent('Not available: no per-rater column');
    expect(screen.getByTestId('tile-paired')).toHaveTextContent('Not available: no group column');
  });

  it('lists failed checks first and strikes a failed metric', () => {
    const r = record();
    r.checks = [...r.checks, { check_id: 'row_alignment', check_version: 1, metric_id: 'auroc', result: 'fail', statistic: {}, rule: 'r', reason: 'misaligned' }];
    render(<RecordCard record={r} />);
    expect(screen.getAllByTestId('check-line')[0]).toHaveAttribute('data-result', 'fail');
    expect(screen.getByTestId('tile-auroc')).toHaveTextContent('Does not measure what it claims: misaligned');
  });

  it('the reliability chart has a table view with counts and greys sparse bins', () => {
    render(<RecordCard record={record()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show as table' }));
    expect(screen.getByTestId('reliability-table')).toHaveTextContent('4 (under 30 rows)');
  });

  it('shows the domain warning text', () => {
    render(<RecordCard record={record({ warnings: [{ kind: 'calibration_domain', message: 'differ in length' }] })} />);
    expect(screen.getByTestId('record-warning')).toHaveTextContent('differ in length');
  });
});

describe('CalibrationPanel', () => {
  it('groups records by question, newest per labeler', () => {
    const older = record({ id: 'cr_old', created_at: '2026-10-01T00:00:00Z' });
    const newer = record({ id: 'cr_new' });
    const other = record({ id: 'cr_b', labeler_identity_hash: 'f'.repeat(64) });
    const groups = latestPerLabeler([older, newer, other]);
    expect(groups).toHaveLength(1);
    expect(groups[0][2].map((r) => r.id).sort()).toEqual(['cr_b', 'cr_new']);
  });

  it('renders records, the target editor and starts a compute job', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/calibration-records': { items: [record()], total: 1 },
      'GET /api/v1/calibration-sets': { items: [{ id: 'cs_1', version_id: 'v1', question: 'Q', question_hash: QH, label_set: ['a', 'b'], source_kind: 'imported', counts: { labeled: 10 }, ratings_sorted: null, licence_class: 'private_only', created_by: 'Ada', created_at: '' }], total: 1 },
      'GET /api/v1/review-queues': { items: [], total: 0 },
      'GET /api/v1/calibration-targets': { question_hash: QH, current: null, default_lower_bound: 0.7, history: [] },
      'POST /api/v1/calibration-records': () => ({ status: 202, json: { job_id: 'job_9', room: 'dataworks/calibration/job_9' } }),
      'PUT /api/v1/calibration-targets': () => ({ status: 201, json: {} }),
    });
    render(<CalibrationPanel panel={getPanel('calibration')} />);
    await waitFor(() => expect(screen.getByTestId('record-card')).toBeInTheDocument());
    expect(screen.getByTestId('target-editor')).toHaveTextContent('default (C3)');
    fireEvent.change(screen.getByLabelText('Calibration set'), { target: { value: 'cs_1' } });
    fireEvent.change(screen.getByLabelText('Label run ID'), { target: { value: 'lr_1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Compute a record' }));
    await waitFor(() => expect(screen.getByTestId('compute-status')).toHaveTextContent('queued'));
    expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/calibration-records')?.body).toEqual({ label_run_id: 'lr_1', calibration_set_id: 'cs_1' });
    fireEvent.change(screen.getByLabelText('New gate target'), { target: { value: '0.8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set target' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ question: record().question, target: 0.8 });
  });
});

describe('AddCalibrationSetDialog', () => {
  it('builds the mapping document from the form', () => {
    expect(toMapping(EMPTY_DRAFT)).toBeNull();
    expect(toMapping({ ...EMPTY_DRAFT, labelColumn: 'meanGrade', positiveAtOrAbove: '1.6', negativeAtOrBelow: '0.4', ratingsColumn: 'grades', groupColumn: 'pair_id', strata: 'kind', referenceColumn: 'kind', referenceValue: 'original' })).toEqual({
      schema: 'dw.calibration-mapping/v1',
      human_label: { column: 'meanGrade', rule: 'numeric', positive_at_or_above: 1.6, negative_at_or_below: 0.4 },
      ratings: { column: 'grades', format: 'digit_string' },
      group: { column: 'pair_id' },
      strata: ['kind'],
      reference: { column: 'kind', value: 'original' },
    });
  });

  it('previews (debounced) and shows the sorted-ratings warning, then creates the set', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/calibration-sets/preview': { counts: { rows: 200, positives: 40, negatives: 60, excluded: 50, references: 50, groups: 50 }, ratings_sorted: true, warnings: ['Rating positions are ranks, not rater identities.'], sample: [], mapping_hash: 'x' },
      'POST /api/v1/calibration-sets/import': () => ({ status: 201, json: { id: 'cs_9' } }),
    });
    const onClose = vi.fn();
    render(<AddCalibrationSetDialog onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('Version ID'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText(/^Question/), { target: { value: 'Funny?' } });
    fireEvent.change(screen.getByLabelText('Human-label column'), { target: { value: 'meanGrade' } });
    fireEvent.change(screen.getByLabelText('Positive at or above'), { target: { value: '1.6' } });
    fireEvent.change(screen.getByLabelText('Negative at or below'), { target: { value: '0.4' } });
    expect(calls.filter((c) => c.path.endsWith('/preview'))).toHaveLength(0);
    await waitFor(() => expect(screen.getByTestId('sorted-warning')).toBeInTheDocument(), { timeout: PREVIEW_DEBOUNCE_MS + 1500 });
    expect(screen.getByTestId('mapping-preview')).toHaveTextContent('200 rows: 40 positive, 60 negative, 50 excluded');
    fireEvent.click(screen.getByRole('button', { name: 'Create calibration set' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(calls.find((c) => c.path.endsWith('/import'))?.body).toMatchObject({ version_id: 'v1', question: 'Funny?', label_set: ['humorous', 'not_humorous'] });
  });
});

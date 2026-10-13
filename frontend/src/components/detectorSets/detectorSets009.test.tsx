// Feature 009's screen (FTASKS 9.2 - 9.10): components, the store through the real API client, and
// the copy audit.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { getPanel } from '@/config/panels';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { mockFetch } from '@/test/fetchMock';
import { CHECKS, CLEAN_CHECKS, RESULTS, SEND, SET } from '@/test/fixtures009';

import { DetectorChecks } from './DetectorChecks';
import { firing, interval } from './format';
import { LabelMappingEditor } from './LabelMappingEditor';
import { LengthProfilePanel } from './LengthProfilePanel';
import { ResultsPanel } from './ResultsPanel';
import { EMPTY_ROLE, RoleEditor, basisFor, parseMapping } from './RoleEditor';
import { RoleTable } from './RoleTable';
import { RunRequestSkeleton } from './RunRequestSkeleton';
import { SendDialog, defaultRepos } from './SendDialog';
import { SendProgress } from './SendProgress';

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  useDetectorSetsStore.setState({ sets: [], current: null, checks: null, send: null, results: null, pendingApproval: null, error: null, notice: null });
});

describe('format', () => {
  it('names the set and sample with every rate', () => {
    expect(firing('funny headlines', 0.3626, 2661)).toBe('funny headlines firing 36.3% (n = 2,661)');
    expect(interval([0.72577, 0.75019])).toBe('[0.7258, 0.7502]');
  });
  it('parses a typed mapping and ignores unknown targets', () => {
    expect(parseMapping('a=positive, b=negative, c=maybe')).toEqual({ a: 'positive', b: 'negative' });
  });
});

describe('RoleTable', () => {
  it('shows each role with its rows, positives / negatives and miStudio view', () => {
    render(<RoleTable roles={SET.roles} checks={CHECKS} />);
    const table = screen.getByTestId('role-table');
    expect(table).toHaveTextContent('Out-of-distribution evaluation');
    expect(table).toHaveTextContent('6,600');
    expect(table).toHaveTextContent('2,661 / 3,939');
    expect(table).toHaveTextContent('eval · out of distribution');
    expect(table).toHaveTextContent('Calibration negatives');
  });
});

describe('LabelMappingEditor', () => {
  it('lists the real values and offers no positive for calibration negatives', () => {
    const onChange = vi.fn();
    render(<LabelMappingEditor values={{ humorous: 3, not_humorous: 4 }} mapping={{}} onChange={onChange} calibration />);
    const select = screen.getByLabelText('Mapping for humorous');
    expect(select.querySelectorAll('option[value="positive"]').length).toBe(0);
    fireEvent.change(select, { target: { value: 'negative' } });
    expect(onChange).toHaveBeenCalledWith({ humorous: 'negative' });
  });
});

describe('DetectorChecks', () => {
  it('labels outcomes in words, not colour only, with next steps', () => {
    render(<DetectorChecks outcomes={CHECKS.outcomes} />);
    expect(screen.getByTestId('check-D-3')).toHaveTextContent('Refused');
    expect(screen.getByTestId('check-D-3')).toHaveTextContent('Next: Balance the training rows');
    expect(screen.getByTestId('check-D-5')).toHaveTextContent('Note');
    expect(screen.getByTestId('check-D-1')).toHaveTextContent('Green');
  });
});

describe('LengthProfilePanel', () => {
  it('prints both profiles with n and the finest false positive rate', () => {
    render(<LengthProfilePanel calibration={CHECKS.profiles.dsr_c} monitored={CHECKS.monitored!.profile} check={CHECKS.outcomes[4]} />);
    expect(screen.getByTestId('profile-Calibration negatives')).toHaveTextContent('n = 2,000 rows');
    expect(screen.getByTestId('profile-Calibration negatives')).toHaveTextContent('1 / 2,000');
    expect(screen.getByTestId('profile-Monitored text')).toHaveTextContent('n = 6,600 rows');
    expect(screen.getByTestId('overlap-note')).toHaveTextContent('2.0% overlap');
  });
});

describe('SendDialog', () => {
  it('is disabled by a refusing check and names its next step', () => {
    render(<SendDialog roles={SET.roles} refusal={CHECKS.first_refusal} checked onSend={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Send to miStudio' })).toBeDisabled();
    expect(screen.getByTestId('send-blocked')).toHaveTextContent('D-3: Balance the training rows');
  });
  it('defaults each version to <namespace>/<dataset>-v<n>, private', () => {
    const onSend = vi.fn();
    render(<SendDialog roles={SET.roles} refusal={null} checked onSend={onSend} />);
    fireEvent.change(screen.getByLabelText('Hugging Face namespace'), { target: { value: 'mistudio' } });
    expect(screen.getByTestId('send-repos')).toHaveTextContent('mistudio/humicroedit-v1');
    fireEvent.click(screen.getByRole('button', { name: 'Send to miStudio' }));
    expect(onSend).toHaveBeenCalledWith({ namespace: 'mistudio', visibility: 'private' });
    expect(defaultRepos(SET.roles, 'ns')).toEqual({ v1: 'ns/humor-balanced-v2', v2: 'ns/humicroedit-v1', v3: 'ns/headline-negatives-v1' });
  });
});

describe('SendProgress and RunRequestSkeleton', () => {
  it('shows each step and the run request in ProbeRunCreate shape', () => {
    render(<SendProgress send={SEND} onResume={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId('send-state')).toHaveTextContent('completed');
    expect(screen.getByTestId('send-progress')).toHaveTextContent('pmd_train');
    render(<RunRequestSkeleton request={SEND.run_request!} mistudioUrl="http://mistudio.test" />);
    expect(screen.getByTestId('run-request')).toHaveTextContent('"eval_dataset_ids"');
    expect(screen.getByRole('link', { name: 'Open miStudio' })).toHaveAttribute('href', 'http://mistudio.test');
  });
  it('offers Resume on a failed send', () => {
    const onResume = vi.fn();
    render(<SendProgress send={{ ...SEND, state: 'failed', error: { code: 'counts_mismatch', message: 'miStudio registered 59', next_step: 'Remove it' } }} onResume={onResume} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onResume).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert')).toHaveTextContent('Next: Remove it');
  });
});

describe('ResultsPanel', () => {
  it('copies the rung words, shows AUROC with interval and n, firing with n, and the paired reason', () => {
    render(<ResultsPanel results={RESULTS} />);
    expect(screen.getByTestId('rung-pill')).toHaveTextContent('miStudio · detects on unseen tasks');
    const probe = screen.getByTestId('probe-pm_935fc9088482');
    expect(probe).toHaveTextContent('0.7375 [0.7258, 0.7502]');
    expect(probe).toHaveTextContent('2,661 / 3,939');
    expect(probe).toHaveTextContent('positives firing 36.3% (n = 2,661)');
    expect(screen.getByTestId('paired-score')).toHaveTextContent('T-47');
  });
  it('never shows a reward probe among evaluations', () => {
    render(<ResultsPanel results={{ ...RESULTS, evaluations: [], training_reward: [{ ...RESULTS.evaluations[0], reward: true }] }} />);
    expect(screen.queryByTestId('rung-pill')).not.toBeInTheDocument();
    expect(screen.getByTestId('reward-badge')).toHaveTextContent('Training reward — not an evaluation');
  });
});

describe('DetectorSetsPanel through the real store and API client', () => {
  it('opens a set, runs checks, sends, and refreshes results', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/detector-sets': { items: [SET], total: 1 },
      'GET /api/v1/detector-sets/dts_1': SET,
      'GET /api/v1/detector-sets/dts_1/results': () => ({ status: 404, json: { error: { code: 'report_not_found', message: 'none', details: {} } } }),
      'POST /api/v1/detector-sets/dts_1/checks': CLEAN_CHECKS,
      'POST /api/v1/detector-sets/dts_1/send': () => ({ status: 202, json: { send_id: 'dsn_1', job_id: 'job_1', state: 'queued' } }),
      'GET /api/v1/detector-sends/dsn_1': SEND,
      'POST /api/v1/detector-sets/dts_1/results/refresh': RESULTS,
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    fireEvent.click(await screen.findByTestId('set-card-humor-set'));
    await screen.findByTestId('set-detail');
    fireEvent.click(screen.getByRole('button', { name: 'Run checks' }));
    await screen.findByTestId('detector-checks');
    fireEvent.change(screen.getByLabelText('Hugging Face namespace'), { target: { value: 'mistudio' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to miStudio' }));
    await screen.findByTestId('run-request');
    const send = calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/detector-sets/dts_1/send');
    expect(send).toHaveLength(1);
    expect(send[0].body).toEqual({ namespace: 'mistudio', visibility: 'private' });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh results' }));
    await screen.findByTestId('results-panel');
    expect(calls.filter((c) => c.path === '/api/v1/detector-sets/dts_1/results/refresh')).toHaveLength(1);
  });

  it('an agent-style 202 with an approval shows the waiting notice', async () => {
    mockFetch({
      'GET /api/v1/detector-sets': { items: [SET], total: 1 },
      'GET /api/v1/detector-sets/dts_1': SET,
      'GET /api/v1/detector-sets/dts_1/results': () => ({ status: 404, json: { error: { code: 'x', message: 'none', details: {} } } }),
      'POST /api/v1/detector-sets/dts_1/checks': CLEAN_CHECKS,
      'POST /api/v1/detector-sets/dts_1/send': () => ({ status: 202, json: { approval_id: 'apr_1', status: 'pending' } }),
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    fireEvent.click(await screen.findByTestId('set-card-humor-set'));
    fireEvent.click(await screen.findByRole('button', { name: 'Run checks' }));
    await screen.findByTestId('detector-checks');
    fireEvent.change(screen.getByLabelText('Hugging Face namespace'), { target: { value: 'mistudio' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to miStudio' }));
    await waitFor(() => expect(screen.getByTestId('send-notice')).toHaveTextContent('Waiting for the operator'));
  });
});

describe('label source columns (2026-10-07 fix, defect 1)', () => {
  it('editing roles keeps each role\'s declaration and sends the edited one', async () => {
    const declared = {
      ...SET,
      roles: SET.roles.map((r) => (r.role === 'train' || r.role === 'id_test' ? { ...r, label_source_columns: ['meanGrade'] } : r)),
    };
    const { calls } = mockFetch({
      'GET /api/v1/detector-sets': { items: [declared], total: 1 },
      'GET /api/v1/detector-sets/dts_1': declared,
      'GET /api/v1/detector-sets/dts_1/results': () => ({ status: 404, json: { error: { code: 'x', message: 'none', details: {} } } }),
      'PATCH /api/v1/detector-sets/dts_1': declared,
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    fireEvent.click(await screen.findByTestId('set-card-humor-set'));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit roles' }));
    const train = screen.getByLabelText('Training rows label source columns');
    expect(train).toHaveValue('meanGrade');
    expect(screen.getByLabelText('In-distribution test label source columns')).toHaveValue('meanGrade');
    fireEvent.change(train, { target: { value: 'meanGrade, grades,' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save roles' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
    const body = calls.find((c) => c.method === 'PATCH')!.body as { roles: Array<{ role: string; label_source_columns?: string[] }> };
    expect(body.roles.find((r) => r.role === 'train')!.label_source_columns).toEqual(['meanGrade', 'grades']);
    expect(body.roles.find((r) => r.role === 'id_test')!.label_source_columns).toEqual(['meanGrade']);
  });
});

describe('human-labelled calibration negatives (2026-10-07 fix, defect 2)', () => {
  it('records the label column, the mapped negatives, who labelled them and the rule', () => {
    const onChange = vi.fn();
    const role = { ...EMPTY_ROLE('calibration_negatives'), label_column: 'human_label', label_mapping: { '0': 'negative' as const, '1': 'excluded' as const, None: 'excluded' as const } };
    const { rerender } = render(<RoleEditor value={role} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Calibration negatives basis'), { target: { value: 'human_labelled' } });
    const chosen = onChange.mock.calls.at(-1)![0];
    expect(chosen.negatives_basis).toEqual({ kind: 'human_labelled', label_column: 'human_label', negative_values: ['0'], labelled_by: '', rule: '' });
    rerender(<RoleEditor value={chosen} onChange={onChange} />);
    expect(screen.getByTestId('human-basis')).toHaveTextContent('Negatives are the rows whose human_label is 0');
    fireEvent.change(screen.getByLabelText('Labelled by'), { target: { value: 'five graders' } });
    expect(onChange.mock.calls.at(-1)![0].negatives_basis.labelled_by).toBe('five graders');
    // a mapping edit keeps the basis's negative values in step with the role
    fireEvent.change(screen.getByLabelText('Calibration negatives label mapping'), { target: { value: '0=negative, 2=negative, 1=excluded' } });
    expect(onChange.mock.calls.at(-1)![0].negatives_basis.negative_values).toEqual(['0', '2']);
    expect(basisFor('human_labelled', { ...role, label_mapping: { '0': 'negative', '2': 'negative' } }).negative_values).toEqual(['0', '2']);
    expect(basisFor('assumed_negative', role)).toEqual({ kind: 'assumed_negative' });
  });
});

describe('copy audit (FTASKS 9.8)', () => {
  it('names calibration negatives in full and uses no forbidden probe word', () => {
    const dir = join(__dirname);
    const sources = readdirSync(dir).filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx')).map((f) => readFileSync(join(dir, f), 'utf8'));
    const all = sources.join('\n') + readFileSync(join(dir, '..', 'panels', 'DetectorSetsPanel.tsx'), 'utf8');
    for (const word of ['causal', 'guarantee', 'validated']) expect(all.toLowerCase()).not.toContain(word);
    expect(all).not.toMatch(/\bsafe\b/i);
    expect(all).not.toMatch(/\bcal(ibration)? neg\b/i);
    expect(all).toContain('Calibration negatives');
  });
});

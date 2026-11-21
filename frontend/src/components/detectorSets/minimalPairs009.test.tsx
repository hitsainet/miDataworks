// Minimal pairs as a chain (009 FTASKS 15.x; operator decision 2026-10-07): the section through the
// real store and API client, every stage shown, a stopped chain resumed where it stopped.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DetectorSetsPanel } from '@/components/panels/DetectorSetsPanel';
import { getPanel } from '@/config/panels';
import { useMinimalPairsStore } from '@/stores/minimalPairsStore';
import { mockFetch } from '@/test/fetchMock';
import { CHAIN_DONE, CHAIN_STOPPED } from '@/test/fixtures009';

import { ChainCard, MinimalPairsSection, STAGE_WORDS } from './MinimalPairs';

afterEach(() => {
  vi.unstubAllGlobals();
  useMinimalPairsStore.setState({ chains: [], refusal: null, error: null });
});

const TEMPLATES = {
  items: [
    { id: 'gt_mp', name: 'minimal-pair-v1', version: 1, ref: 'minimal-pair-v1@1', kind: 'respond', description: null, body: {}, content_hash: 'a'.repeat(64), builtin: true, used: false, placeholders: ['prompt'] },
    { id: 'gt_r', name: 'respond-v1', version: 1, ref: 'respond-v1@1', kind: 'respond', description: null, body: {}, content_hash: 'b'.repeat(64), builtin: true, used: false, placeholders: ['prompt'] },
  ],
  total: 2,
};

describe('ChainCard', () => {
  it('shows every stage with its run or version, and names the stage a chain stopped at', () => {
    render(<ChainCard chain={CHAIN_STOPPED} onResume={vi.fn()} onCancel={vi.fn()} />);
    for (const stage of ['generate', 'scope', 'judge', 'pair']) {
      expect(screen.getByTestId(`stage-${stage}`)).toHaveTextContent(STAGE_WORDS[stage]);
    }
    expect(screen.getByTestId('stage-generate')).toHaveTextContent('run gr_1');
    expect(screen.getByTestId('stage-scope')).toHaveTextContent('version v_scope');
    expect(screen.getByTestId('stage-judge')).toHaveTextContent('Stopped here');
    expect(screen.getByTestId('chain-error')).toHaveTextContent('MODEL_NOT_LOADED');
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  it('a completed chain reports verified pairs and every unverified one by reason', () => {
    render(<ChainCard chain={CHAIN_DONE} onResume={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByTestId('chain-counts')).toHaveTextContent('2 of 4 judged pairs verified; version v_pairs.');
    expect(screen.getByTestId('chain-counts')).toHaveTextContent('1 dropped: the judge did not read the edit as the target verdict');
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
  });
});

describe('MinimalPairsSection through the real store and API client', () => {
  it('resumes a stopped chain with one call and shows what came back', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/minimal-pair-chains': { items: [CHAIN_STOPPED], total: 1 },
      'POST /api/v1/minimal-pair-chains/mpc_1/resume': CHAIN_DONE,
    });
    render(<MinimalPairsSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Resume' }));
    await screen.findByTestId('chain-counts');
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/minimal-pair-chains/mpc_1/resume')).toHaveLength(1);
  });

  it('starts a chain with the minimal-pair template by default, and shows a refusal with its code', async () => {
    let refuse = true;
    const { calls } = mockFetch({
      'GET /api/v1/minimal-pair-chains': { items: [], total: 0 },
      'GET /api/v1/generation-templates': TEMPLATES,
      'POST /api/v1/minimal-pair-chains': () =>
        refuse
          ? { status: 422, json: { error: { code: 'JUDGE_IS_GENERATOR', message: 'The judge is the same model as the generator.', details: {} } } }
          : { status: 202, json: { ...CHAIN_STOPPED, state: 'running', failed_stage: null, error: null, stage: 'generate' } },
    });
    render(<MinimalPairsSection />);
    await screen.findByTestId('no-chains');
    fireEvent.click(screen.getByRole('button', { name: 'New minimal pairs' }));
    fireEvent.change(screen.getByLabelText('Seed version ID'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('Judge rubric ID'), { target: { value: 'rb_1' } });
    fireEvent.change(screen.getByLabelText('Largest edit in words'), { target: { value: '3' } });
    await waitFor(() => expect(screen.getByLabelText('Edit template')).toHaveValue('gt_mp'));
    fireEvent.click(screen.getByRole('button', { name: 'Start minimal pairs' }));
    await waitFor(() => expect(screen.getByTestId('chain-refusal')).toHaveTextContent('JUDGE_IS_GENERATOR'));
    refuse = false;
    fireEvent.click(screen.getByRole('button', { name: 'Start minimal pairs' }));
    await screen.findByTestId('chain-mpc_1');
    const starts = calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/minimal-pair-chains');
    expect(starts).toHaveLength(2);
    expect(starts[1].body).toEqual({
      input_version_id: 'v1', text_column: 'text', seed_splits: ['train'], sample_size: 100,
      respond_template_id: 'gt_mp', rubric_id: 'rb_1', flip_from: 'yes', flip_to: 'no', max_edit_words: 3,
    });
  });

  it('cancels a running chain', async () => {
    const running = { ...CHAIN_STOPPED, state: 'running' as const, failed_stage: null, error: null, resumable: false };
    const { calls } = mockFetch({
      'GET /api/v1/minimal-pair-chains': { items: [running], total: 1 },
      'POST /api/v1/minimal-pair-chains/mpc_1/cancel': { ...running, state: 'cancelled', resumable: true },
    });
    render(<MinimalPairsSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    await screen.findByRole('button', { name: 'Resume' });
    expect(calls.filter((c) => c.path === '/api/v1/minimal-pair-chains/mpc_1/cancel')).toHaveLength(1);
  });
});

describe('the Detector sets screen carries the section (wiring)', () => {
  it('the live panel registry entry renders Minimal pairs with the chains it reads', async () => {
    mockFetch({
      'GET /api/v1/detector-sets': { items: [], total: 0 },
      'GET /api/v1/minimal-pair-chains': { items: [CHAIN_STOPPED], total: 1 },
    });
    render(<DetectorSetsPanel panel={getPanel('detector-sets')} />);
    expect(await screen.findByTestId('chain-mpc_1')).toBeInTheDocument();
    expect(screen.getByTestId('minimal-pairs')).toHaveTextContent('Minimal pairs');
  });
});

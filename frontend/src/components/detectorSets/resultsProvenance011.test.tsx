// Feature 011 in the results panel (FR-011.8, FR-011.11, FPRD section 4): one commit badge per role
// in three states, the scan note, and a paired score's source with the skipped reasons.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RESULTS } from '@/test/fixtures009';
import type { CommitRow, ResultsSnapshot } from '@/types/detectorSets';

import { ResultsPanel } from './ResultsPanel';

const COMMIT = 'c'.repeat(40);

function row(check: CommitRow['commit_check'], phrase: string): CommitRow {
  return {
    role_id: 'dsr_ood', role: 'ood_eval', probe_dataset_id: 'pmd_ood', view_name: 'humor-set - out-of-distribution evaluation',
    repo_id: 'mistudio/humicroedit-v1', split: 'test', commit: COMMIT, mistudio_dataset_id: 'ds-1',
    commit_check: check, commit_check_phrase: phrase,
  };
}

function snap(over: Partial<ResultsSnapshot>): ResultsSnapshot {
  return { ...RESULTS, run_discovery: { method: 'filter', limit: 200 }, commits: [row('verified', 'verified by miStudio')], ...over };
}

describe('ResultsPanel provenance (feature 011)', () => {
  it.each([
    ['verified', 'verified by miStudio'],
    ['claimed', 'claimed (miStudio does not pin downloads)'],
    [null, 'not checked (sent before feature 011)'],
  ] as const)('shows the %s commit badge with its phrase and the short commit', (check, phrase) => {
    render(<ResultsPanel results={snap({ commits: [row(check, phrase)] })} />);
    const badge = screen.getByTestId('commit-check');
    expect(badge).toHaveTextContent(phrase);
    expect(badge).toHaveAttribute('data-check', check ?? 'none');
    expect(screen.getByTestId('commit-ood_eval')).toHaveTextContent(COMMIT.slice(0, 12));
    expect(screen.getByTestId('commit-ood_eval')).toHaveTextContent('Out-of-distribution evaluation');
  });

  it('says commits are not recorded on a snapshot from before 011', () => {
    render(<ResultsPanel results={snap({ commits: null, run_discovery: null })} />);
    expect(screen.getByTestId('commits-not-recorded')).toHaveTextContent('not recorded');
    expect(screen.getByTestId('run-discovery-not-recorded')).toBeInTheDocument();
    expect(screen.queryByTestId('commit-check')).not.toBeInTheDocument();
  });

  it('shows the scan note, and no note when miStudio filtered the runs', () => {
    const note = 'scanned the newest 200 runs; older runs may be missing';
    const { unmount } = render(<ResultsPanel results={snap({ run_discovery: { method: 'scan', limit: 200, note } })} />);
    expect(screen.getByTestId('run-discovery-note')).toHaveTextContent(note);
    unmount();
    render(<ResultsPanel results={snap({})} />);
    expect(screen.queryByTestId('run-discovery-note')).not.toBeInTheDocument();
    expect(screen.queryByTestId('run-discovery-not-recorded')).not.toBeInTheDocument();
  });

  it("names the paired score's source and keeps the skipped reasons in its title", () => {
    const [figure] = RESULTS.evaluations;
    const set = {
      ...figure.sets[0],
      paired: {
        available: true, paired: 0.6123, ci: [0.58, 0.64] as [number, number], pairs: 412, source: 'millm' as const,
        skipped: [{ source: 'mistudio' as const, reason: 'miStudio: Evaluation pme_1 was recorded before miStudio saved row positions' }],
      },
    };
    render(<ResultsPanel results={snap({ evaluations: [{ ...figure, sets: [set] }] })} />);
    const cell = screen.getByTestId('paired-score');
    expect(cell).toHaveTextContent('61.2% [0.5800, 0.6400], 412 pairs · from miLLM scoring');
    expect(cell).toHaveAttribute('title', 'mistudio: miStudio: Evaluation pme_1 was recorded before miStudio saved row positions');
  });

  it("says a miStudio-sourced score is from miStudio's evaluation", () => {
    const [figure] = RESULTS.evaluations;
    const set = { ...figure.sets[0], paired: { available: true, paired: 0.7, ci: [0.6, 0.8] as [number, number], pairs: 10, source: 'mistudio' as const, skipped: [] } };
    render(<ResultsPanel results={snap({ evaluations: [{ ...figure, sets: [set] }] })} />);
    const cell = screen.getByTestId('paired-score');
    expect(cell).toHaveTextContent("from miStudio's evaluation");
    expect(cell).not.toHaveAttribute('title');
  });
});

// Label runs (FR-005.49; FPRD 005 section 4.2; mockup `LabelRuns`): cards on the left, the open
// run's detail on the right. Progress arrives over the run's room, with polling while disconnected.
import { useEffect, useState } from 'react';

import { ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { Button } from '@/components/common/Button';
import { LabelRunCard } from '@/components/labeling/LabelRunCard';
import { LabelRunDetail } from '@/components/labeling/LabelRunDetail';
import { RubricLibrary } from '@/components/labeling/RubricLibrary';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useLabelRunsStore } from '@/stores/labelRunsStore';

export function LabelRunsPanel({ panel }: { panel: PanelDef }) {
  const list = useLabelRunsStore((s) => s.list);
  const byId = useLabelRunsStore((s) => s.byId);
  const live = useLabelRunsStore((s) => s.live);
  const selectedId = useLabelRunsStore((s) => s.selectedId);
  const error = useLabelRunsStore((s) => s.error);
  const fetchRuns = useLabelRunsStore((s) => s.fetchRuns);
  const selectRun = useLabelRunsStore((s) => s.selectRun);
  const includeArchived = useLabelRunsStore((s) => s.includeArchived);
  const setIncludeArchived = useLabelRunsStore((s) => s.setIncludeArchived);
  const [showRubrics, setShowRubrics] = useState(false);

  useEffect(() => {
    void fetchRuns();
  }, [fetchRuns]);

  const selected = selectedId ? byId[selectedId] : undefined;
  return (
    <>
      <PageHead help="label"
        title={panel.title}
        subtitle={panel.subtitle}
        right={<Button size="sm" variant={showRubrics ? 'primary' : 'secondary'} aria-pressed={showRubrics} onClick={() => setShowRubrics((v) => !v)}>Rubrics</Button>}
      />
      {showRubrics && <div className="mb-6"><RubricLibrary /></div>}
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="mb-3"><ShowArchivedToggle checked={includeArchived} onChange={(v) => void setIncludeArchived(v)} testId="show-archived-label-runs" /></div>
      {/* A run just archived leaves the list but stays open, so Unarchive is one click away. */}
      {list.length === 0 && !selected ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-runs">
          {includeArchived ? 'No label runs, archived or not.' : 'No label runs yet. Start one from the Label step of a new dataset.'}
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-5">
          <div className="lg:col-span-2 space-y-3" data-testid="label-run-list">
            {list.length === 0 && <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-runs">{includeArchived ? 'No label runs, archived or not.' : 'No other label runs.'}</p>}
            {list.map((run) => <LabelRunCard key={run.id} run={run} live={live[run.id]} selected={run.id === selectedId} onOpen={() => {
              selectRun(run.id);
              // On a narrow screen the detail sits below the list: bring it into view.
              if (window.matchMedia?.('(max-width: 1023px)').matches) document.querySelector('[data-testid="label-run-detail-column"]')?.scrollIntoView?.({ block: 'start' });
            }} />)}
          </div>
          <div className="lg:col-span-3 min-w-0 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto" data-testid="label-run-detail-column">
            {selected ? <LabelRunDetail run={selected} /> : <p className="text-sm text-slate-500 dark:text-slate-400">Open a run to see its labels and provenance.</p>}
          </div>
        </div>
      )}
    </>
  );
}

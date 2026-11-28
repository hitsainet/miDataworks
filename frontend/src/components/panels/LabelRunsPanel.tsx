// Label runs (FR-005.49; FPRD 005 section 4.2; mockup `LabelRuns`): cards on the left, the open
// run's detail on the right. The open run follows its room (polling while disconnected); the list
// re-reads every few seconds while the page is visible, so tiles move and new runs appear without a
// browser refresh (operators, 2026-10-31 and 2026-11-27), as on the Generation page.
import { useEffect, useState } from 'react';

import { BehaviourFilter } from '@/components/behaviour/Behaviour017';
import { ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { toggleOpen, useEscapeToClose } from '@/components/common/DetailPopup';
import { Button } from '@/components/common/Button';
import { PurgeBar, PurgeBlockerNote, PurgeItemCheckbox, usePurgeSelection } from '@/components/common/PurgeControls';
import { LabelRunCard } from '@/components/labeling/LabelRunCard';
import { LabelRunDetail } from '@/components/labeling/LabelRunDetail';
import { RubricLibrary } from '@/components/labeling/RubricLibrary';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { LABEL_RUN_POLL_MS } from '@/hooks/useLabelRun';
import { useListPoll } from '@/hooks/useListPoll';
import { useLabelRunsStore } from '@/stores/labelRunsStore';
import type { LabelRun } from '@/types/labeling';

const runName = (run: LabelRun) =>
  run.input_dataset_name ? `label run over ${run.input_dataset_name} v${run.input_version_number ?? '?'}` : `label run ${run.id}`;

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
  const behaviourKey = useLabelRunsStore((s) => s.behaviourKey);
  const setBehaviourKey = useLabelRunsStore((s) => s.setBehaviourKey);
  const [showRubrics, setShowRubrics] = useState(false);

  useEffect(() => {
    void fetchRuns();
  }, [fetchRuns]);

  // Only the open run follows its room, so the list itself is re-read every few seconds while the
  // page is open and visible: every tile moves while its run goes, and a run started elsewhere (an
  // agent through MCP) appears without a browser refresh (operator, 2026-11-27). The per-run reads
  // this replaces stopped once no listed run was unfinished, so a new run never arrived.
  useListPoll(() => fetchRuns({ quiet: true }), LABEL_RUN_POLL_MS);

  const purge = usePurgeSelection(
    'label_run',
    list.map((run) => ({ id: run.id, name: runName(run), archived: Boolean(run.archived_at) })),
    includeArchived,
  );
  const onPurged = async (ids: string[]) => {
    if (selectedId && ids.includes(selectedId)) selectRun(null);
    await fetchRuns();
  };

  const selected = selectedId ? byId[selectedId] : undefined;
  const closeRun = () => selectRun(null);
  useEscapeToClose(Boolean(selected), closeRun);
  return (
    <>
      <PageHead help="label"
        title={panel.title}
        subtitle={panel.subtitle}
        right={<Button size="sm" variant={showRubrics ? 'primary' : 'secondary'} aria-pressed={showRubrics} onClick={() => setShowRubrics((v) => !v)}>Rubrics</Button>}
      />
      {showRubrics && <div className="mb-6"><RubricLibrary /></div>}
      {error && <div role="alert" className="mb-4 rounded-card border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <ShowArchivedToggle checked={includeArchived} onChange={(v) => void setIncludeArchived(v)} testId="show-archived-label-runs" />
        <BehaviourFilter value={behaviourKey} onChange={(k) => void setBehaviourKey(k)} testId="runs-behaviour-filter" />
      </div>
      {includeArchived && <div className="mb-3"><PurgeBar selection={purge} onPurged={onPurged} /></div>}
      {/* A run just archived leaves the list but stays open, so Unarchive is one click away. */}
      {list.length === 0 && !selected ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-runs">
          {includeArchived ? 'No label runs, archived or not.' : 'No label runs yet. Start one from the Label step of a new dataset.'}
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-5">
          <div className="lg:col-span-2 stack-y-3" data-testid="label-run-list">
            {list.length === 0 && <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-runs">{includeArchived ? 'No label runs, archived or not.' : 'No other label runs.'}</p>}
            {list.map((run) => (
              <div key={run.id} className="flex gap-2 items-start" data-testid="label-run-row">
                <PurgeItemCheckbox selection={purge} id={run.id} name={runName(run)} />
                <div className="min-w-0 flex-1">
                  <LabelRunCard run={run} live={live[run.id]} selected={run.id === selectedId} onOpen={() => {
                    // The open run's card closes it; another card switches (operator, 2026-10-31).
                    const next = toggleOpen(selectedId, run.id);
                    selectRun(next);
                    if (!next) return;
                    // On a narrow screen the detail sits below the list: bring it into view.
                    if (window.matchMedia?.('(max-width: 1023px)').matches) document.querySelector('[data-testid="label-run-detail-column"]')?.scrollIntoView?.({ block: 'start' });
                  }} />
                  <PurgeBlockerNote selection={purge} id={run.id} />
                </div>
              </div>
            ))}
          </div>
          <div className="lg:col-span-3 min-w-0 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto" data-testid="label-run-detail-column">
            {selected ? <LabelRunDetail run={selected} onClose={closeRun} /> : <p className="text-sm text-slate-500 dark:text-slate-400">Open a run to see its labels and provenance.</p>}
          </div>
        </div>
      )}
    </>
  );
}

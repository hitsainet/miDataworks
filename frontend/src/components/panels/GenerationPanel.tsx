// Generation (FR-007.44; FPRD 007 section 4.2): run cards on the left, the open run (or a new-run
// form) on the right. Progress arrives over the run's room, with polling while disconnected.
import { useEffect, useMemo, useState } from 'react';

import { ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { Button } from '@/components/common/Button';
import { PurgeBar, PurgeBlockerNote, PurgeItemCheckbox, usePurgeSelection } from '@/components/common/PurgeControls';
import { GenerationRunCard } from '@/components/generation/GenerationRunCard';
import { GenerationRunDetail } from '@/components/generation/GenerationRunDetail';
import { NewStandardRunForm } from '@/components/generation/NewStandardRunForm';
import { NewSteeredPairForm } from '@/components/generation/NewSteeredPairForm';
import { TemplateLibrary } from '@/components/generation/TemplateLibrary';
import { NO_BEHAVIOUR, behaviourLabel } from '@/components/generation/runSummary';
import { STATE_LABEL } from '@/components/generation/format';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useGenerationStore } from '@/stores/generationStore';
import type { GenerationRun } from '@/types/generation';

type Pane = 'run' | 'standard' | 'steered' | 'templates';
type Sort = 'newest' | 'oldest' | 'behaviour';

function behaviourOf(run: GenerationRun): string {
  return behaviourLabel(run.behaviour) ?? NO_BEHAVIOUR;
}

const SELECT = 'w-auto max-w-[11rem] rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-xs';

export function GenerationPanel({ panel }: { panel: PanelDef }) {
  const list = useGenerationStore((s) => s.list);
  const byId = useGenerationStore((s) => s.byId);
  const live = useGenerationStore((s) => s.live);
  const selectedId = useGenerationStore((s) => s.selectedId);
  const error = useGenerationStore((s) => s.error);
  const loadRuns = useGenerationStore((s) => s.loadRuns);
  const selectRun = useGenerationStore((s) => s.selectRun);
  const includeArchived = useGenerationStore((s) => s.includeArchived);
  const setIncludeArchived = useGenerationStore((s) => s.setIncludeArchived);
  const [pane, setPane] = useState<Pane>('run');
  const [stateFilter, setStateFilter] = useState('');
  const [behaviourFilter, setBehaviourFilter] = useState('');
  const [sort, setSort] = useState<Sort>('newest');

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const purge = usePurgeSelection(
    'generation_run',
    list.map((run) => ({ id: run.id, name: `${run.mode.replace(/_/g, ' ')} generation run ${run.id}`, archived: Boolean(run.archived_at) })),
    includeArchived,
  );
  const onPurged = async (ids: string[]) => {
    if (selectedId && ids.includes(selectedId)) selectRun(null);
    await loadRuns();
  };

  // Filter by status and behaviour, sort newest first by default (operator request 2026-10-21).
  const behaviours = useMemo(() => [...new Set(list.map(behaviourOf))].sort(), [list]);
  const states = useMemo(() => [...new Set(list.map((r) => r.state))], [list]);
  const shown = useMemo(() => {
    const rows = list.filter((r) => (!stateFilter || r.state === stateFilter) && (!behaviourFilter || behaviourOf(r) === behaviourFilter));
    const time = (r: GenerationRun) => Date.parse(r.created_at);
    return [...rows].sort((a, b) =>
      sort === 'oldest' ? time(a) - time(b)
        : sort === 'behaviour' ? behaviourOf(a).localeCompare(behaviourOf(b)) || time(b) - time(a)
        : time(b) - time(a));
  }, [list, stateFilter, behaviourFilter, sort]);

  const selected = selectedId ? byId[selectedId] : undefined;
  return (
    <>
      <PageHead
        help="generation"
        title={panel.title}
        subtitle={panel.subtitle}
        right={
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setPane('standard')}>New standard run</Button>
            <Button size="sm" variant="secondary" onClick={() => setPane('steered')}>New steered-pair run</Button>
            <Button size="sm" variant="secondary" onClick={() => setPane('templates')}>Templates</Button>
          </div>
        }
      />
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="lg:col-span-2 stack-y-3" data-testid="generation-run-list">
          <ShowArchivedToggle checked={includeArchived} onChange={(v) => void setIncludeArchived(v)} testId="show-archived-generation-runs" />
          {includeArchived && <PurgeBar selection={purge} onPurged={onPurged} />}
          {list.length > 0 && (
            <div className="flex flex-wrap gap-2 items-center text-xs text-slate-500 dark:text-slate-400" data-testid="generation-run-filters">
              <label className="flex items-center gap-1">Status
                <select className={SELECT} value={stateFilter} onChange={(e) => setStateFilter(e.target.value)} data-testid="filter-state">
                  <option value="">All</option>
                  {states.map((s) => <option key={s} value={s}>{STATE_LABEL[s] ?? s}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-1">Behaviour
                <select className={SELECT} value={behaviourFilter} onChange={(e) => setBehaviourFilter(e.target.value)} data-testid="filter-behaviour">
                  <option value="">All</option>
                  {behaviours.map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-1">Sort
                <select className={SELECT} value={sort} onChange={(e) => setSort(e.target.value as Sort)} data-testid="sort-runs">
                  <option value="newest">Newest first</option>
                  <option value="oldest">Oldest first</option>
                  <option value="behaviour">By behaviour</option>
                </select>
              </label>
              {shown.length !== list.length && <span>{shown.length} of {list.length} runs shown</span>}
            </div>
          )}
          {list.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-generation-runs">
              {includeArchived ? 'No generation runs, archived or not.' : 'No generation runs yet. Start one from a version with a held-out split.'}
            </p>
          ) : (
            shown.map((run) => (
              <div key={run.id} className="flex gap-2 items-start" data-testid="generation-run-row">
                <PurgeItemCheckbox selection={purge} id={run.id} name={`generation run ${run.id}`} />
                <div className="min-w-0 flex-1">
                  <GenerationRunCard run={run} live={live[run.id]} selected={run.id === selectedId} onOpen={() => { selectRun(run.id); setPane('run'); }} />
                  <PurgeBlockerNote selection={purge} id={run.id} />
                </div>
              </div>
            ))
          )}
        </div>
        <div className="lg:col-span-3 min-w-0">
          {pane === 'standard' && <NewStandardRunForm onStarted={() => setPane('run')} />}
          {pane === 'steered' && <NewSteeredPairForm onStarted={() => setPane('run')} />}
          {pane === 'templates' && <TemplateLibrary />}
          {pane === 'run' && (selected ? <GenerationRunDetail run={selected} /> : <p className="text-sm text-slate-500 dark:text-slate-400">Open a run to see its records and pairs.</p>)}
        </div>
      </div>
    </>
  );
}

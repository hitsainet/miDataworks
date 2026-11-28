// Generation (FR-007.44; FPRD 007 section 4.2): run cards on the left, the open run (or a new-run
// form) on the right. The open run's progress arrives over its room, with polling while
// disconnected; the list re-reads every few seconds while the page is visible (hooks/useListPoll).
//
// The top of the page is pinned (operator request 2026-11-27: "pin the top of the Generation page
// from the Sort box up"): the heading, its subtitle, the Help and Manual links, the three buttons,
// Show archived, the Status and Behaviour filters and Sort stay put while the window scrolls the
// runs beneath them. The block is sticky under the top bar and, when it shows, under the approvals
// banner's dock (`--approvals-dock-height`), open or collapsed, so it never covers the banner. It is
// opaque in both themes and registers as pinned (components/layout/pinned.ts), so a run card reached
// by Tab or Shift+Tab is scrolled into view below it, not behind it.
//
// Short windows and phones: the block is capped, never unpinned. Its height is at most 60% of the
// viewport less the top bar and the dock (never under 4rem), so the runs keep about 40% of the window.
// Past the cap the block scrolls inside itself and its last part, the list controls (Show archived
// to Sort), sticks to its bottom edge, so the controls stay in view and the heading and buttons
// stay reachable by the block's own scroll. The DOM order, so the focus order, is unchanged.
//
// The right-hand pane (operator, 2026-11-27: a run opened from far down the list showed its detail at
// the top of the right-hand column, scrolled out of view, so nothing seemed to happen): in the
// two-column layout the pane is sticky right below the pinned block (`--shell-pinned-height`, the
// dock and the block together) and, taller than the space left, scrolls inside itself. A long run
// form opens there too, so every field and button of it is reached by the pane's own scroll (and by
// Tab, which scrolls the pane). In one column (narrower than `lg`) the pane sits below every run,
// so opening a run or a form scrolls the pane into view below the pinned block.
//
// The filters wrap (operator, 2026-11-27, a 390 px phone with the sidebar expanded cut Status,
// Behaviour and Sort off on the right): each label wraps above its select and a select shrinks to
// the width there is, so none is ever cut off, whatever the sidebar state.
import { useEffect, useMemo, useRef, useState } from 'react';

import { ShowArchivedToggle } from '@/components/common/ArchiveControls';
import { toggleOpen, useEscapeToClose } from '@/components/common/DetailPopup';
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
import { usePinned } from '@/components/layout/pinned';
import { GENERATION_POLL_MS } from '@/hooks/useGenerationRun';
import { useListPoll } from '@/hooks/useListPoll';
import type { PanelDef } from '@/config/panels';
import { useGenerationStore } from '@/stores/generationStore';
import type { GenerationRun } from '@/types/generation';

type Pane = 'run' | 'standard' | 'steered' | 'templates';
type Sort = 'newest' | 'oldest' | 'behaviour';

function behaviourOf(run: GenerationRun): string {
  return behaviourLabel(run.behaviour) ?? NO_BEHAVIOUR;
}

/** The pinned block: sticky under the top bar (3.5rem) and the approvals dock, opaque, and capped so
 *  the runs keep about 40% of a short window: at most 60% of the viewport less the top bar and the
 *  dock, never under 4rem (`--generation-pinned-cap`). It scrolls inside itself only past the cap;
 *  its own scroll padding keeps a focused heading control clear of the controls stuck to its bottom. */
export const GENERATION_PINNED_CLASS =
  'sticky top-[calc(3.5rem_+_var(--approvals-dock-height,0px))] z-10 -mt-2 mb-4 overflow-y-auto ' +
  'border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-950 pt-2 ' +
  '[--generation-pinned-cap:max(4rem,calc(60dvh_-_3.5rem_-_var(--approvals-dock-height,0px)))] ' +
  'max-h-(--generation-pinned-cap) scroll-pb-[calc(var(--generation-controls-height,0px)_+_0.25rem)]';
/** The list controls, the block's last part: stuck to its bottom edge when the block scrolls. Never
 *  taller than the cap less 2.5rem, so the heading keeps a window above them; past that (a phone with
 *  the banner open) the controls scroll inside themselves, and every control stays reachable. */
export const GENERATION_CONTROLS_CLASS =
  'sticky bottom-0 bg-slate-50 dark:bg-slate-950 pb-3 stack-y-3 overflow-y-auto ' +
  'max-h-[calc(var(--generation-pinned-cap)_-_2.5rem)]';
/** The list controls' own height, for the block's bottom scroll padding. */
export const GENERATION_CONTROLS_HEIGHT_VAR = '--generation-controls-height';

/** The right-hand pane, in two columns (`lg`): sticky right below the pinned stack, where it starts
 *  unscrolled (the block's 1rem bottom margin less this pane's 4px inset), so it never jumps; never
 *  taller than the window below that, and scrolling inside itself past it. Its bottom stays 1.75rem
 *  above the window's, clear of the screen's 1.5rem bottom padding: a taller pane would be pushed up
 *  by the end of its column when the window is scrolled to the end, and its top would go under the
 *  pinned block. The 4px inset (`p-1`, offset by `-m-1`) keeps a focus ring (2px ring, 2px offset)
 *  inside the pane's scrollport. */
export const GENERATION_PANE_CLASS =
  'lg:sticky lg:top-[calc(3.5rem_+_var(--shell-pinned-height,0px)_+_0.75rem)] ' +
  'lg:max-h-[calc(100dvh_-_3.5rem_-_var(--shell-pinned-height,0px)_-_2.5rem)] lg:overflow-y-auto lg:-m-1 lg:p-1';

/** Scrolls the pane into view below the pinned stack when its top is out of sight: the one-column
 *  layout, where the pane sits below every run. In two columns the pane is sticky and already in
 *  view, so this does nothing. */
export function revealPane(pane: HTMLElement, pinned: HTMLElement | null): void {
  const top = pane.getBoundingClientRect().top;
  const below = pinned ? pinned.getBoundingClientRect().bottom : 0;
  if ((top < below || top > window.innerHeight - 48) && typeof pane.scrollIntoView === 'function') {
    pane.scrollIntoView({ block: 'start' });
  }
}

/** A filter: its label wraps above the select when the row is narrow, and the select shrinks. */
const FILTER_LABEL = 'flex flex-wrap items-center gap-1 min-w-0 max-w-full';
const SELECT = 'w-auto min-w-0 max-w-[min(11rem,100%)] rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-xs';

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
  const pinnedRef = usePinned();
  const controlsRef = usePinned(GENERATION_CONTROLS_HEIGHT_VAR, false);
  const pinnedEl = useRef<HTMLDivElement | null>(null);
  const paneEl = useRef<HTMLDivElement>(null);
  // Bumped when the operator opens a run or a form, so the pane is brought into view then only.
  const [reveal, setReveal] = useState(0);
  useEffect(() => {
    if (reveal > 0 && paneEl.current) revealPane(paneEl.current, pinnedEl.current);
  }, [reveal]);
  const show = (next: Pane) => {
    setPane(next);
    setReveal((n) => n + 1);
  };
  const pinnedRefs = (element: HTMLDivElement | null) => {
    pinnedEl.current = element;
    return pinnedRef(element);
  };

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  // The list itself is re-read every few seconds while the page is open and visible, so a run
  // started elsewhere (an agent through MCP) appears without a browser refresh (operator,
  // 2026-11-27), and every tile's counts and progress move while its run goes (017 FTASKS 17.8 (a);
  // only the open run follows its room). One list read replaces the per-run reads of each
  // unfinished tile, which never saw a run the page did not already hold.
  useListPoll(() => loadRuns({ quiet: true }), GENERATION_POLL_MS);

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
  // A tile opens its run; the open run's tile closes it; another tile switches (operator,
  // 2026-10-31). The X and Escape close it too (components/common/DetailPopup.tsx).
  const openRun = (id: string) => {
    const next = pane === 'run' ? toggleOpen(selectedId, id) : id;
    selectRun(next);
    setPane('run');
    if (next) setReveal((n) => n + 1);
  };
  const closeRun = () => selectRun(null);
  useEscapeToClose(pane === 'run' && Boolean(selected), closeRun);
  return (
    <>
      <div ref={pinnedRefs} className={GENERATION_PINNED_CLASS} data-testid="generation-pinned">
        <PageHead
          help="generation"
          title={panel.title}
          subtitle={panel.subtitle}
          right={
            <div className="flex flex-wrap gap-2" data-testid="generation-actions">
              <Button size="sm" onClick={() => show('standard')}>New standard run</Button>
              <Button size="sm" variant="secondary" onClick={() => show('steered')}>New steered-pair run</Button>
              <Button size="sm" variant="secondary" onClick={() => show('templates')}>Templates</Button>
            </div>
          }
        />
        {error && <div role="alert" className="mb-4 rounded-card border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
        <div ref={controlsRef} className={GENERATION_CONTROLS_CLASS} data-testid="generation-list-controls">
          <div className="flex flex-wrap gap-x-5 gap-y-2 items-center">
            <ShowArchivedToggle checked={includeArchived} onChange={(v) => void setIncludeArchived(v)} testId="show-archived-generation-runs" />
            {list.length > 0 && (
              <div className="flex flex-wrap gap-2 items-center min-w-0 max-w-full text-xs text-slate-500 dark:text-slate-400" data-testid="generation-run-filters">
                <label className={FILTER_LABEL}>Status
                  <select className={SELECT} value={stateFilter} onChange={(e) => setStateFilter(e.target.value)} data-testid="filter-state">
                    <option value="">All</option>
                    {states.map((s) => <option key={s} value={s}>{STATE_LABEL[s] ?? s}</option>)}
                  </select>
                </label>
                <label className={FILTER_LABEL}>Behaviour
                  <select className={SELECT} value={behaviourFilter} onChange={(e) => setBehaviourFilter(e.target.value)} data-testid="filter-behaviour">
                    <option value="">All</option>
                    {behaviours.map((b) => <option key={b} value={b}>{b}</option>)}
                  </select>
                </label>
                <label className={FILTER_LABEL}>Sort
                  <select className={SELECT} value={sort} onChange={(e) => setSort(e.target.value as Sort)} data-testid="sort-runs">
                    <option value="newest">Newest first</option>
                    <option value="oldest">Oldest first</option>
                    <option value="behaviour">By behaviour</option>
                  </select>
                </label>
                {shown.length !== list.length && <span>{shown.length} of {list.length} runs shown</span>}
              </div>
            )}
          </div>
          {includeArchived && <PurgeBar selection={purge} onPurged={onPurged} />}
        </div>
      </div>
      <div className="grid gap-5 lg:grid-cols-5">
        <div className="lg:col-span-2 stack-y-3" data-testid="generation-run-list">
          {list.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-generation-runs">
              {includeArchived ? 'No generation runs, archived or not.' : 'No generation runs yet. Start one from a version with a held-out split.'}
            </p>
          ) : (
            shown.map((run) => (
              <div key={run.id} className="flex gap-2 items-start" data-testid="generation-run-row">
                <PurgeItemCheckbox selection={purge} id={run.id} name={`generation run ${run.id}`} />
                <div className="min-w-0 flex-1">
                  <GenerationRunCard run={run} live={live[run.id]} selected={pane === 'run' && run.id === selectedId} onOpen={() => openRun(run.id)} />
                  <PurgeBlockerNote selection={purge} id={run.id} />
                </div>
              </div>
            ))
          )}
        </div>
        <div className="lg:col-span-3 min-w-0">
          <div ref={paneEl} className={GENERATION_PANE_CLASS} data-testid="generation-pane">
            {pane === 'standard' && <NewStandardRunForm onStarted={() => setPane('run')} />}
            {pane === 'steered' && <NewSteeredPairForm onStarted={() => setPane('run')} />}
            {pane === 'templates' && <TemplateLibrary />}
            {pane === 'run' && (selected ? <GenerationRunDetail run={selected} onClose={closeRun} /> : <p className="text-sm text-slate-500 dark:text-slate-400">Open a run to see its records and pairs.</p>)}
          </div>
        </div>
      </div>
    </>
  );
}

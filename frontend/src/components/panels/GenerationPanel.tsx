// Generation (FR-007.44; FPRD 007 section 4.2): run cards on the left, the open run (or a new-run
// form) on the right. Progress arrives over the run's room, with polling while disconnected.
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { GenerationRunCard } from '@/components/generation/GenerationRunCard';
import { GenerationRunDetail } from '@/components/generation/GenerationRunDetail';
import { NewStandardRunForm } from '@/components/generation/NewStandardRunForm';
import { NewSteeredPairForm } from '@/components/generation/NewSteeredPairForm';
import { TemplateLibrary } from '@/components/generation/TemplateLibrary';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useGenerationStore } from '@/stores/generationStore';

type Pane = 'run' | 'standard' | 'steered' | 'templates';

export function GenerationPanel({ panel }: { panel: PanelDef }) {
  const list = useGenerationStore((s) => s.list);
  const byId = useGenerationStore((s) => s.byId);
  const live = useGenerationStore((s) => s.live);
  const selectedId = useGenerationStore((s) => s.selectedId);
  const error = useGenerationStore((s) => s.error);
  const loadRuns = useGenerationStore((s) => s.loadRuns);
  const selectRun = useGenerationStore((s) => s.selectRun);
  const [pane, setPane] = useState<Pane>('run');

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const selected = selectedId ? byId[selectedId] : undefined;
  return (
    <>
      <PageHead
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
        <div className="lg:col-span-2 space-y-3" data-testid="generation-run-list">
          {list.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-generation-runs">No generation runs yet. Start one from a version with a held-out split.</p>
          ) : (
            list.map((run) => (
              <GenerationRunCard key={run.id} run={run} live={live[run.id]} selected={run.id === selectedId} onOpen={() => { selectRun(run.id); setPane('run'); }} />
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

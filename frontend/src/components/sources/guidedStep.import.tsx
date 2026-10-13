// Feature 001's Import step of 002's guided flow (FR-001.37; 002 FTDD 6.3 convention): the same
// form, upload and detection as the Datasets screen. When the import yields a ready source, its id
// and detection go to 002's draft (one draftPatchFor, shared with the Datasets screen).
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';
import { useSourceImports } from '@/hooks/useSourceImports';
import { useDraftsStore } from '@/stores/draftsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { ImportOutcome } from '@/stores/sourcesStore';
import type { SourceDetail } from '@/types/sources';

import { DetectionPanel } from './DetectionPanel';
import { draftPatchFor } from './handoff';
import { ImportForm } from './ImportForm';
import { UploadCard } from './UploadCard';

function ImportStep({ draft, onAdvance }: GuidedStepProps) {
  useSourceImports();
  const fetchSourcesMeta = useSourcesStore((s) => s.fetchSourcesMeta);
  const fetchSource = useSourcesStore((s) => s.fetchSource);
  const imports = useSourcesStore((s) => s.imports);
  const updateDraft = useDraftsStore((s) => s.updateDraft);
  const [jobId, setJobId] = useState<string | null>(null);
  const [source, setSource] = useState<SourceDetail | null>(null);

  useEffect(() => {
    void fetchSourcesMeta();
  }, [fetchSourcesMeta]);

  const finished = jobId ? imports[jobId] : undefined;
  useEffect(() => {
    if (finished?.status === 'completed' && finished.sourceId) void fetchSource(finished.sourceId).then(setSource);
  }, [finished?.status, finished?.sourceId, fetchSource]);

  const take = (outcome: ImportOutcome) => {
    if (outcome.kind === 'started') setJobId(outcome.jobId);
    if (outcome.kind === 'existing') setSource(outcome.source);
  };

  const useSource = () => {
    if (!source) return;
    updateDraft(draftPatchFor(draft, source));
    onAdvance();
  };

  return (
    <div className="space-y-4" data-testid="guided-import">
      <ImportForm onOutcome={take} />
      <UploadCard onOutcome={take} />
      {finished && finished.status !== 'completed' && <p role="status" className="text-sm text-slate-500 dark:text-slate-400">{finished.status === 'failed' ? finished.error : finished.phase ?? 'Importing…'}</p>}
      {source && (
        <section className="rounded-xl border border-slate-200 dark:border-slate-700 p-4">
          <h3 className="font-semibold mb-2">{source.display_name} is ready</h3>
          <DetectionPanel detection={source.detection} />
          <div className="flex justify-end mt-3"><Button onClick={useSource} disabled={source.state !== 'ready'}>Use this source</Button></div>
        </section>
      )}
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'import', order: 0, Component: ImportStep };
export default panel;

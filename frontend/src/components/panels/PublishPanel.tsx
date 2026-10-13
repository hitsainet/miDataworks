// Publish and export (R-03.60; FPRD 008 section 4; FTID 008 section 6). Layout from the mockup's
// `Exports`: the publish form spans three columns, the checks two; the export card, the model-terms
// card and the history follow. The publish button names its action and stays disabled while the
// checks for the CURRENT choice are pending or, for a public push, while any is amber.
import { ScanSearch, Upload } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { PageHead } from '@/components/layout/PageHead';
import { CardEditor } from '@/components/publishing/CardEditor';
import { ChecksPanel } from '@/components/publishing/ChecksPanel';
import { ExportPanel } from '@/components/publishing/ExportPanel';
import { FilePreview } from '@/components/publishing/FilePreview';
import { ModelTermsDialog } from '@/components/publishing/ModelTermsDialog';
import { PublishHistory } from '@/components/publishing/PublishHistory';
import { publishGate } from '@/components/publishing/publishRules';
import type { PanelDef } from '@/config/panels';
import { usePublishJob } from '@/hooks/usePublishJob';
import { usePublishStore } from '@/stores/publishStore';
import { useVersionsStore } from '@/stores/versionsStore';

export const CHECKS_DEBOUNCE_MS = 500;
const REPO_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;

export function PublishPanel({ panel }: { panel: PanelDef }) {
  const selection = usePublishStore((s) => s.selection);
  const build = usePublishStore((s) => s.build);
  const checkRun = usePublishStore((s) => s.checkRun);
  const draft = usePublishStore((s) => s.draft);
  const job = usePublishStore((s) => s.job);
  const lastPublish = usePublishStore((s) => s.lastPublish);
  const publishes = usePublishStore((s) => s.publishes);
  const exports = usePublishStore((s) => s.exports);
  const terms = usePublishStore((s) => s.terms);
  const error = usePublishStore((s) => s.error);
  const notice = usePublishStore((s) => s.notice);
  const setSelection = usePublishStore((s) => s.setSelection);
  const previewFiles = usePublishStore((s) => s.previewFiles);
  const runChecks = usePublishStore((s) => s.runChecks);
  const publish = usePublishStore((s) => s.publish);
  const reverify = usePublishStore((s) => s.reverify);
  const startExport = usePublishStore((s) => s.startExport);
  const fetchHistory = usePublishStore((s) => s.fetchHistory);
  const fetchTerms = usePublishStore((s) => s.fetchTerms);
  const addTermsNote = usePublishStore((s) => s.addTermsNote);
  const versionList = useVersionsStore((s) => s.versionList);
  const fetchVersionList = useVersionsStore((s) => s.fetchVersionList);
  usePublishJob();

  useEffect(() => {
    void fetchVersionList();
    void fetchHistory();
  }, [fetchVersionList, fetchHistory]);

  const jobRunning = Boolean(job && !['completed', 'failed', 'cancelled'].includes(job.status));
  const repoValid = REPO_ID.test(selection.repoId);
  const buildReady = build?.status === 'completed';

  // Checks start on their own when their inputs change, debounced; the server content-addresses
  // builds, and a check run is cheap to repeat.
  useEffect(() => {
    if (!buildReady || !repoValid || checkRun || jobRunning) return undefined;
    const timer = setTimeout(() => void runChecks(), CHECKS_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [buildReady, repoValid, checkRun, jobRunning, runChecks, selection.visibility, selection.repoId]);

  const gate = publishGate(checkRun, selection.visibility, jobRunning && job?.kind === 'publish');
  const field = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  const repoPublic = gate.effective === 'public' && selection.visibility === 'private';

  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      {notice && <div role="status" className="mb-4 rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50 dark:bg-indigo-500/10 px-4 py-2 text-sm">{notice}</div>}
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="grid gap-5 lg:grid-cols-5 mb-6">
        <Card className="lg:col-span-3 min-w-0" data-testid="publish-form">
          <div className="font-semibold mb-4">Publish to Hugging Face</div>
          <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2 mb-4">
            <label className="text-sm">
              <span className="block font-medium mb-1">Version</span>
              <select aria-label="Version" className={field} value={selection.versionId} onChange={(e) => setSelection({ versionId: e.target.value })}>
                <option value="">Choose a version</option>
                {versionList
                  .filter((v) => v.state === 'completed')
                  .map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.dataset_name} v{v.number}
                    </option>
                  ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="block font-medium mb-1">Repository</span>
              <input aria-label="Repository" className={`${field} font-mono`} value={selection.repoId} placeholder="namespace/name" onChange={(e) => setSelection({ repoId: e.target.value.trim() })} />
              <span className="block text-xs text-slate-500 dark:text-slate-400 mt-1">Created if it does not exist. Republishing adds a commit.</span>
            </label>
            <label className="text-sm">
              <span className="block font-medium mb-1">Visibility</span>
              <select aria-label="Visibility" className={field} value={selection.visibility} onChange={(e) => setSelection({ visibility: e.target.value as 'private' | 'public' })}>
                <option value="private">Private</option>
                <option value="public">Public</option>
              </select>
              <span className="block text-xs text-slate-500 dark:text-slate-400 mt-1">Private unless you choose otherwise.</span>
            </label>
            <label className="text-sm">
              <span className="block font-medium mb-1">Label column</span>
              <input aria-label="Label column" className={`${field} font-mono`} value={selection.labelColumn} placeholder="label (optional)" onChange={(e) => setSelection({ labelColumn: e.target.value.trim() })} />
              <span className="block text-xs text-slate-500 dark:text-slate-400 mt-1">Counted per split in the card; review overrides apply to it.</span>
            </label>
          </div>
          {repoPublic && (
            <div role="status" className="mb-3 rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm" data-testid="repo-public">
              {selection.repoId} already exists and is public, so this push is public and the public checks apply. Choose another repository to publish privately.
            </div>
          )}
          <FilePreview build={build} />
          <CardEditor draft={draft} prose={selection.prose} onProse={(prose) => setSelection({ prose })} />
          {job && jobRunning && (
            <div className="mb-3 text-sm" data-testid="job-progress">
              <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400">
                <span>{job.message ?? job.kind}</span>
                <span>{Math.round(job.progress)}% · {job.status}</span>
              </div>
              <div className="h-1.5 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden" role="progressbar" aria-valuenow={Math.round(job.progress)} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full bg-indigo-500" style={{ width: `${job.progress}%` }} />
              </div>
            </div>
          )}
          {lastPublish && (
            <div className="mb-3 text-sm" data-testid="publish-result">
              {lastPublish.status === 'published'
                ? `Published ${lastPublish.repo_id} at ${lastPublish.commit?.slice(0, 8)} (${lastPublish.visibility_after}); every file's hash matched.`
                : `${lastPublish.status.replace('_', ' ')}: ${lastPublish.error?.message ?? ''}`}
            </div>
          )}
          <div className="flex flex-wrap gap-2 justify-end">
            <Button variant="secondary" leftIcon={<ScanSearch className="w-4 h-4" />} disabled={!selection.versionId || jobRunning} onClick={() => void previewFiles()}>
              Preview files
            </Button>
            <span title={gate.blocker ?? undefined} data-testid="publish-blocker" data-blocker={gate.blocker ?? ''}>
              <Button leftIcon={<Upload className="w-4 h-4" />} disabled={gate.disabled || !repoValid} onClick={() => void publish()}>
                {gate.label}
              </Button>
            </span>
          </div>
          {gate.blocker && <div className="mt-2 text-xs text-right text-slate-500 dark:text-slate-400">{gate.blocker}</div>}
        </Card>
        <ChecksPanel run={checkRun} pending={jobRunning && job?.kind === 'publish_check'} />
      </div>
      <ExportPanel versionId={selection.versionId} labelColumn={selection.labelColumn} onExport={(r) => void startExport(r)} />
      <ModelTermsDialog terms={terms} onLookup={(id) => void fetchTerms(id)} onRecord={(id, value, text) => void addTermsNote(id, value, text)} />
      <div className="text-base font-semibold mb-3">Published and exported</div>
      <PublishHistory publishes={publishes} exports={exports} onReverify={(id) => void reverify(id)} />
      <div className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        Large files are stored on the Hub with Git LFS (Large File Storage); small files by their git hash. AUROC (area under the receiver operating characteristic curve) figures in a card come from calibration records.
      </div>
    </>
  );
}

// Version detail (FR-002.42): header "<dataset> vN", actions, stat tiles, drop log, lineage, row
// browser with "Why did this row leave?", slots from other features, and Compare (FR-002.43).
import { CheckCircle2, GitBranch, ScanSearch, Trash2, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { EmptyState } from '@/components/common/EmptyState';
import { PageHead } from '@/components/layout/PageHead';
import { CompareView } from '@/components/versions/CompareView';
import { DropLogCard } from '@/components/versions/DropLogCard';
import { LineagePanel } from '@/components/versions/LineagePanel';
import { RowBrowser } from '@/components/versions/RowBrowser';
import { StatTiles } from '@/components/versions/StatTiles';
import { VersionSlotHost } from '@/components/versions/VersionSlotHost';
import type { PanelDef } from '@/config/panels';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useRecipesStore } from '@/stores/recipesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { CompareReport } from '@/types/versions';
import { navigate } from '@/utils/navigate';

export function VersionDetailPanel({ panel }: { panel: PanelDef }) {
  const selectedId = useVersionsStore((s) => s.selectedId);
  const versions = useVersionsStore((s) => s.versions);
  const versionList = useVersionsStore((s) => s.versionList);
  const lineage = useVersionsStore((s) => s.lineage);
  const error = useVersionsStore((s) => s.error);
  const loading = useVersionsStore((s) => s.loading);
  const selectVersion = useVersionsStore((s) => s.selectVersion);
  const fetchVersionList = useVersionsStore((s) => s.fetchVersionList);
  const fetchVersion = useVersionsStore((s) => s.fetchVersion);
  const fetchLineage = useVersionsStore((s) => s.fetchLineage);
  const fetchCompare = useVersionsStore((s) => s.fetchCompare);
  const verifyRebuild = useVersionsStore((s) => s.verifyRebuild);
  const deleteVersion = useVersionsStore((s) => s.deleteVersion);
  const meta = useDatasetsStore((s) => s.meta);
  const fetchMeta = useDatasetsStore((s) => s.fetchMeta);
  const cloneRecipe = useRecipesStore((s) => s.cloneRecipe);
  const [compare, setCompare] = useState<CompareReport | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [action, setAction] = useState<'clone' | 'delete' | null>(null);
  const [text, setText] = useState('');

  useEffect(() => {
    void fetchMeta();
    void fetchVersionList();
  }, [fetchMeta, fetchVersionList]);
  // Another version drops the comparison (adjusted during render, not in an effect).
  const [compareFor, setCompareFor] = useState(selectedId);
  if (compareFor !== selectedId) {
    setCompareFor(selectedId);
    setCompare(null);
  }
  useEffect(() => {
    if (selectedId) {
      void fetchVersion(selectedId);
      void fetchLineage(selectedId);
    }
  }, [selectedId, fetchVersion, fetchLineage]);

  const version = selectedId ? versions[selectedId] : undefined;
  const picker = (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-slate-500 dark:text-slate-400">Version</span>
      <select
        value={selectedId ?? ''}
        onChange={(e) => selectVersion(e.target.value || null)}
        className="rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-indigo-500"
        aria-label="Choose a version"
      >
        <option value="">Choose a version</option>
        {versionList.map((v) => <option key={v.id} value={v.id}>{v.dataset_name} v{v.number}{v.state === 'deleted' ? ' (deleted)' : ''}</option>)}
      </select>
    </label>
  );

  if (!version) {
    return (
      <>
        <PageHead title={panel.title} subtitle={panel.subtitle} right={picker} />
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
          <EmptyState
            title={loading ? 'Loading the version' : 'No version selected'}
            description={error ?? 'Choose a version above, or open one from a dataset card on the Datasets screen.'}
          />
        </div>
      </>
    );
  }

  const previous = versionList.find((v) => v.dataset_id === version.dataset_id && v.number === version.number - 1);
  const compareTarget = version.parent_version_id ?? previous?.id;
  const compareLabel = version.parent_version_id
    ? `Compare with v${versionList.find((v) => v.id === version.parent_version_id)?.number ?? version.number - 1}`
    : `Compare with v${version.number - 1}`;
  const status = version.state === 'deleted' ? 'Deleted' : version.is_head ? 'Head' : `Superseded by v${version.superseded_by}`;

  const runAction = async () => {
    if (action === 'clone' && version.recipe_id && text.trim()) {
      const recipe = await cloneRecipe(version.recipe_id, text.trim(), version.recipe_revision_id);
      if (recipe) setNotice(`Cloned the recipe into ${recipe.name}. Open Recipes to edit it.`);
    }
    if (action === 'delete' && text.trim()) {
      if (await deleteVersion(version.id, text.trim())) setNotice(`Deleted version ${version.number}. Its manifest, counts and events are kept.`);
    }
    setAction(null);
    setText('');
  };

  return (
    <>
      <PageHead
        title={`${version.dataset_name} v${version.number}`}
        subtitle={`${status} · ${version.target_type} · ${version.total_rows.toLocaleString('en-US')} rows · seed ${version.seed} · built by ${version.created_by}`}
        right={
          <div className="flex flex-wrap gap-2">
            {picker}
            <Button variant="secondary" leftIcon={<GitBranch className="w-4 h-4" />} onClick={() => { setAction('clone'); setText(''); }} disabled={!version.recipe_id}>Clone recipe</Button>
            {compareTarget && (
              <Button variant="secondary" leftIcon={<ScanSearch className="w-4 h-4" />} onClick={async () => setCompare(await fetchCompare(version.id, version.parent_version_id ? undefined : compareTarget))} disabled={version.state === 'deleted'}>
                {compareLabel}
              </Button>
            )}
            <Button variant="secondary" leftIcon={<CheckCircle2 className="w-4 h-4" />} disabled={version.state === 'deleted'} onClick={async () => { const job = await verifyRebuild(version.id); if (job) setNotice(`Verify rebuild started (job ${job}). It rebuilds without reuse and compares digests.`); }}>
              Verify rebuild
            </Button>
            <Button variant="danger" leftIcon={<Trash2 className="w-4 h-4" />} disabled={version.state === 'deleted'} onClick={() => { setAction('delete'); setText(''); }}>Delete version {version.number}</Button>
            <Button leftIcon={<Upload className="w-4 h-4" />} onClick={() => navigate('publish')}>Export</Button>
          </div>
        }
      />
      {notice && <div role="status" className="mb-4 rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50 dark:bg-indigo-500/10 px-4 py-2 text-sm">{notice}</div>}
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      {action && (
        <div className="mb-4 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 flex flex-wrap items-end gap-2 text-sm">
          <label className="flex-1 min-w-[14rem]">
            <span className="block text-xs text-slate-500 dark:text-slate-400 mb-1">{action === 'clone' ? 'Name for the new recipe' : 'Why delete this version? (recorded)'}</span>
            <input autoFocus value={text} onChange={(e) => setText(e.target.value)} className="w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-indigo-500" />
          </label>
          <Button variant={action === 'delete' ? 'danger' : 'primary'} onClick={() => void runAction()} disabled={!text.trim()}>
            {action === 'clone' ? 'Clone recipe' : `Delete version ${version.number}`}
          </Button>
          <Button variant="ghost" onClick={() => setAction(null)}>Cancel</Button>
        </div>
      )}
      {version.warnings.map((w) => (
        <div key={w.code} role="status" className="mb-4 rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-4 py-2 text-sm">
          <span className="font-medium">Warning: </span>{w.message}
        </div>
      ))}
      <StatTiles version={version} meta={meta} />
      {compare && <CompareView report={compare} />}
      <div className="grid gap-5 lg:grid-cols-2 mb-5">
        <DropLogCard steps={version.drop_summary} />
        {lineage && lineage.version_id === version.id ? <LineagePanel lineage={lineage} onOpenVersion={selectVersion} /> : <div />}
      </div>
      <VersionSlotHost version={version} />
      <RowBrowser version={version} />
    </>
  );
}

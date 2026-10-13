// The Datasets screen (T-01; FR-001.32, FR-001.33). Feature 001 owns the import form, upload and the
// sources list; feature 002 owns the card grid below them (FR-002.44). Layout after miStudio's
// DatasetsPanel (form and upload side by side at lg). A card opens the dataset's head version.
import { Plus } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/common/Button';
import { DatasetCardGrid } from '@/components/datasets/DatasetCardGrid';
import { PageHead } from '@/components/layout/PageHead';
import { ImportForm } from '@/components/sources/ImportForm';
import { SourceDetailDrawer } from '@/components/sources/SourceDetailDrawer';
import { SourcesList } from '@/components/sources/SourcesList';
import { UploadCard } from '@/components/sources/UploadCard';
import type { PanelDef } from '@/config/panels';
import { useSourceImports } from '@/hooks/useSourceImports';
import { useDatasetsStore } from '@/stores/datasetsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { DatasetSummary } from '@/types/versions';
import { navigate } from '@/utils/navigate';

export function DatasetsPanel({ panel }: { panel: PanelDef }) {
  const datasets = useDatasetsStore((s) => s.datasets);
  const error = useDatasetsStore((s) => s.error);
  const fetchDatasets = useDatasetsStore((s) => s.fetchDatasets);
  const selectVersion = useVersionsStore((s) => s.selectVersion);
  const fetchSourcesMeta = useSourcesStore((s) => s.fetchSourcesMeta);
  const fetchSources = useSourcesStore((s) => s.fetchSources);
  const fetchSource = useSourcesStore((s) => s.fetchSource);
  const notice = useSourcesStore((s) => s.notice);
  const sourcesError = useSourcesStore((s) => s.error);
  const selected = useSourcesStore((s) => s.selected);
  useSourceImports();

  useEffect(() => {
    void fetchDatasets();
    void fetchSourcesMeta();
    void fetchSources();
  }, [fetchDatasets, fetchSourcesMeta, fetchSources]);

  const open = (d: DatasetSummary) => {
    if (d.head_version_id) {
      selectVersion(d.head_version_id);
      navigate('version');
    } else {
      navigate('new-dataset');
    }
  };

  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} right={<Button leftIcon={<Plus className="w-4 h-4" />} onClick={() => navigate('new-dataset')}>New dataset</Button>} />
      {notice && <div role="status" className="mb-4 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/60 px-4 py-2 text-sm" data-testid="sources-notice">{notice}</div>}
      {sourcesError && !selected && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{sourcesError}</div>}
      <div className="grid gap-6 lg:grid-cols-2 mb-6">
        <ImportForm />
        <UploadCard />
      </div>
      <SourcesList onOpen={(s) => void fetchSource(s.id)} />
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <DatasetCardGrid datasets={datasets} onOpen={open} />
      <SourceDetailDrawer />
    </>
  );
}

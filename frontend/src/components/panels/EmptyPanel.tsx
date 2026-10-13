// A registered screen whose feature has not been built yet (Foundation F2): an empty state that
// names the feature that builds it, rather than a blank page or a fabricated example.
import { EmptyState } from '@/components/common/EmptyState';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';

export function EmptyPanel({ panel }: { panel: PanelDef }) {
  const Icon = panel.icon;
  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <EmptyState
          icon={<Icon className="w-6 h-6 text-indigo-600 dark:text-indigo-400" aria-hidden="true" />}
          title="This screen is not built yet"
          description={`Feature ${panel.feature} builds it. The navigation, colour modes and job tracking around it already work.`}
        />
      </div>
    </>
  );
}

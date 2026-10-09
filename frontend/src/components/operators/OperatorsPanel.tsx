// The Operators screen (FR-003.22; R-03.60): the live catalogue with provider, kind and state
// filters; an operator's detail; the allowlist; and the guide to adding a package (T-10).
import { BookOpen } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useOperatorsStore } from '@/stores/operatorsStore';
import type { OperatorEntry } from '@/types/operators';

import { AddPackageGuide } from './AddPackageGuide';
import { AllowlistSection } from './AllowlistSection';
import { OperatorCard, providerLabel, STATE_TEXT } from './OperatorCard';
import { OperatorDetail } from './OperatorDetail';

export const SUBTITLE = 'Every step is an operator behind one interface. Providers plug in; a recipe names operators, never libraries.';
const PROVIDERS = ['native', 'datajuicer', 'data_designer'];
const KINDS = ['filter', 'mapper', 'deduplicator', 'selector', 'labeler', 'generator', 'report', 'exporter'];

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string]>; onChange: (v: string) => void }) {
  const id = `operator-filter-${label.toLowerCase()}`;
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
      {label}
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1 text-sm text-slate-900 dark:text-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-400"
      >
        <option value="">All</option>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export function OperatorsPanel({ panel }: { panel: PanelDef }) {
  const catalogue = useOperatorsStore((s) => s.catalogue);
  const summary = useOperatorsStore((s) => s.summary);
  const filters = useOperatorsStore((s) => s.filters);
  const loading = useOperatorsStore((s) => s.catalogueLoading);
  const error = useOperatorsStore((s) => s.catalogueError);
  const selected = useOperatorsStore((s) => s.selected);
  const selectedError = useOperatorsStore((s) => s.selectedError);
  const allowlist = useOperatorsStore((s) => s.allowlist);
  const allowlistError = useOperatorsStore((s) => s.allowlistError);
  const fetchCatalogue = useOperatorsStore((s) => s.fetchCatalogue);
  const setFilter = useOperatorsStore((s) => s.setFilter);
  const fetchSubset = useOperatorsStore((s) => s.fetchSubset);
  const selectOperator = useOperatorsStore((s) => s.selectOperator);
  const clearSelection = useOperatorsStore((s) => s.clearSelection);
  const fetchAllowlist = useOperatorsStore((s) => s.fetchAllowlist);
  const changeAllowlist = useOperatorsStore((s) => s.changeAllowlist);
  const [guide, setGuide] = useState(false);

  useEffect(() => {
    void fetchCatalogue();
    void fetchSubset();
    void fetchAllowlist();
  }, [fetchCatalogue, fetchSubset, fetchAllowlist]);

  const open = (entry: OperatorEntry) => void selectOperator(entry.name, entry.version);
  const extraProviders = [...new Set(catalogue.map((e) => e.provider))].filter((p) => !PROVIDERS.includes(p));

  return (
    <div>
      <PageHead
        title={panel.title}
        subtitle={SUBTITLE}
        right={
          <Button variant="secondary" leftIcon={<BookOpen size={16} />} onClick={() => setGuide(true)}>
            How to add an operator package
          </Button>
        }
      />
      <AddPackageGuide open={guide} onClose={() => setGuide(false)} />
      {selected ? (
        <OperatorDetail entry={selected} onBack={clearSelection} />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-4">
            <FilterSelect label="Provider" value={filters.provider ?? ''} options={[...PROVIDERS, ...extraProviders].map((p) => [p, providerLabel(p)])} onChange={(v) => void setFilter('provider', v)} />
            <FilterSelect label="Kind" value={filters.kind ?? ''} options={KINDS.map((k) => [k, k])} onChange={(v) => void setFilter('kind', v)} />
            <FilterSelect
              label="State"
              value={filters.state ?? ''}
              options={Object.entries(STATE_TEXT)}
              onChange={(v) => void setFilter('state', v)}
            />
            <span className="text-xs text-slate-500 dark:text-slate-400" data-testid="operator-summary">
              {summary.allowed ?? 0} allowed · {summary.not_allowed ?? 0} not allowed ·{' '}
              {(summary.invalid_manifest ?? 0) + (summary.failed_to_load ?? 0) + (summary.duplicate ?? 0)} cannot run
            </span>
          </div>
          {selectedError ? <p role="alert" className="mb-3 text-sm text-red-500">{selectedError}</p> : null}
          {error ? (
            <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-500" data-testid="operators-error">
              The catalogue could not be read: {error} Check that the backend is running, then reload.
            </div>
          ) : loading && !catalogue.length ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="operators-loading">Loading the operator catalogue…</p>
          ) : !catalogue.length ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="operators-empty">
              No operator matches these filters. Clear a filter to see more.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {catalogue.map((entry) => (
                <OperatorCard key={`${entry.ref}-${entry.origin}`} entry={entry} onOpen={open} />
              ))}
            </div>
          )}
          <h2 className="mt-8 mb-3 text-sm font-semibold text-slate-800 dark:text-slate-200">Third-party operator packages</h2>
          <AllowlistSection
            items={allowlist}
            error={allowlistError}
            onChange={(action, item, reason) =>
              changeAllowlist(action, {
                distribution: item.distribution,
                distribution_version: item.distribution_version,
                entry_point: item.entry_point,
                reason,
              })
            }
          />
        </>
      )}
    </div>
  );
}

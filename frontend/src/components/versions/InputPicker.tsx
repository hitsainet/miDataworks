// A build's input, chosen by name (novice walk 2026-10-19, finding 3): the Recipes screen's build
// form asked for a free-text "Input ID" that the UI showed nowhere. This lists the ready sources and
// the completed versions by name, newest first, and shows the chosen one's ID with a copy button.
import { useEffect } from 'react';

import { useSourcesStore } from '@/stores/sourcesStore';
import { useVersionsStore } from '@/stores/versionsStore';
import type { InputRef } from '@/types/versions';

import { IdentifierChip } from './IdentifierChip';

const field = 'w-full rounded-sm border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';

export function inputKey(input: InputRef | null): string {
  if (!input) return '';
  return input.kind === 'source' ? `source:${input.source_id}` : `version:${input.version_id}`;
}

export function inputFromKey(key: string): InputRef | null {
  const [kind, id] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)];
  if (!id) return null;
  if (kind === 'source') return { kind: 'source', source_id: id };
  if (kind === 'version') return { kind: 'version', version_id: id };
  return null;
}

export function InputPicker({ value, onChange, label = 'Build from' }: { value: InputRef | null; onChange: (input: InputRef | null) => void; label?: string }) {
  const sources = useSourcesStore((s) => s.sources);
  const fetchSources = useSourcesStore((s) => s.fetchSources);
  const versions = useVersionsStore((s) => s.versionList);
  const fetchVersionList = useVersionsStore((s) => s.fetchVersionList);
  useEffect(() => {
    void fetchSources();
    void fetchVersionList();
  }, [fetchSources, fetchVersionList]);
  const ready = sources.filter((s) => s.state === 'ready');
  const completed = versions.filter((v) => v.state === 'completed');
  const chosenId = value ? (value.kind === 'source' ? value.source_id : value.version_id) : null;
  return (
    <label className="text-xs block">
      <span className="block text-slate-500 dark:text-slate-400 mb-1">{label}</span>
      <select aria-label={label} value={inputKey(value)} onChange={(e) => onChange(inputFromKey(e.target.value))} className={field}>
        <option value="">{ready.length || completed.length ? 'Choose a source or a version' : 'No ready source or version yet: import one on Datasets'}</option>
        {ready.length > 0 && (
          <optgroup label="Sources (imported data)">
            {ready.map((s) => <option key={s.id} value={`source:${s.id}`}>{s.display_name}{s.config ? ` (${s.config})` : ''} · {s.rows.toLocaleString('en-US')} rows</option>)}
          </optgroup>
        )}
        {completed.length > 0 && (
          <optgroup label="Versions (newest first)">
            {completed.map((v) => <option key={v.id} value={`version:${v.id}`}>{v.dataset_name} v{v.number} · {v.total_rows.toLocaleString('en-US')} rows</option>)}
          </optgroup>
        )}
      </select>
      {chosenId && <span className="mt-1 block" data-testid="input-picker-id"><IdentifierChip value={chosenId} label={value?.kind === 'source' ? 'source ID' : 'version ID'} length={36} /></span>}
    </label>
  );
}

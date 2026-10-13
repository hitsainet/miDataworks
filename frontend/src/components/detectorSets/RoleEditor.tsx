// Bind one role to a version split: columns, label mapping, pair column, calibration basis.
import { useState } from 'react';

import type { DetectorRole, MappingTarget, NegativesBasis, RoleIn } from '@/types/detectorSets';

import { ROLE_WORDS } from './format';
import { LabelMappingEditor } from './LabelMappingEditor';

const input =
  'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export const EMPTY_ROLE = (role: DetectorRole): RoleIn => ({
  role,
  version_id: '',
  split: role === 'train' ? 'train' : 'test',
  input_column: 'text',
  label_column: 'label',
  label_mapping: {},
  pair_column: null,
  label_source_columns: [],
  negatives_basis: role === 'calibration_negatives' ? { kind: 'assumed_negative' } : null,
});

/** The values a role's mapping sends to negative: what a human-labelled basis selects by. */
export function negativeValues(mapping: Record<string, MappingTarget>): string[] {
  return Object.entries(mapping)
    .filter(([, target]) => target === 'negative')
    .map(([k]) => k)
    .sort();
}

/** A fresh basis of `kind`, keeping the rule; a human basis names this role's column and negatives. */
export function basisFor(kind: NegativesBasis['kind'], role: RoleIn): NegativesBasis {
  const rule = role.negatives_basis?.rule ?? '';
  if (kind === 'assumed_negative') return { kind };
  if (kind === 'labeler_filtered') return { kind, labeler_identity_hash: role.negatives_basis?.labeler_identity_hash ?? '', rule };
  return { kind, label_column: role.label_column, negative_values: negativeValues(role.label_mapping), labelled_by: role.negatives_basis?.labelled_by ?? '', rule };
}

/** Parse "meanGrade, grades" into column names (blank parts dropped). */
export function parseColumns(text: string): string[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** Parse "value=positive, other=negative" into a mapping (used before the checks list values). */
export function parseMapping(text: string): Record<string, MappingTarget> {
  const out: Record<string, MappingTarget> = {};
  for (const part of text.split(',')) {
    const [k, v] = part.split('=').map((s) => s.trim());
    if (k && (v === 'positive' || v === 'negative' || v === 'excluded')) out[k] = v;
  }
  return out;
}

export function RoleEditor({
  value,
  values,
  onChange,
  onRemove,
}: {
  value: RoleIn;
  values?: Record<string, number>;
  onChange: (next: RoleIn) => void;
  onRemove?: () => void;
}) {
  const [mappingText, setMappingText] = useState(
    Object.entries(value.label_mapping)
      .map(([k, v]) => `${k}=${v}`)
      .join(', '),
  );
  // A human-labelled basis describes THIS role: keep its column and negative values in step.
  const change = (next: RoleIn) =>
    onChange(
      next.negatives_basis?.kind === 'human_labelled'
        ? { ...next, negatives_basis: { ...next.negatives_basis, label_column: next.label_column, negative_values: negativeValues(next.label_mapping) } }
        : next,
    );
  const [sourcesText, setSourcesText] = useState((value.label_source_columns ?? []).join(', '));
  const field = (key: keyof RoleIn, label: string) => (
    <label className="text-xs text-slate-600 dark:text-slate-300">
      {label}
      <input
        aria-label={`${ROLE_WORDS[value.role]} ${label}`}
        className={input}
        value={(value[key] as string | null) ?? ''}
        onChange={(e) => change({ ...value, [key]: e.target.value || (key === 'pair_column' ? null : '') })}
      />
    </label>
  );
  return (
    <fieldset className="rounded-lg border border-slate-200 dark:border-slate-700/60 p-3" data-testid={`role-editor-${value.role}`}>
      <legend className="px-1 text-sm font-medium">{ROLE_WORDS[value.role]}</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {field('version_id', 'Version ID')}
        {field('split', 'Split')}
        {field('input_column', 'Input column')}
        {field('label_column', 'Label column')}
        {field('pair_column', 'Pair column (optional)')}
      </div>
      {(value.role === 'train' || value.role === 'id_test') && (
        <label className="mt-2 block text-xs text-slate-600 dark:text-slate-300">
          Label computed from (optional, comma-separated columns)
          <input
            aria-label={`${ROLE_WORDS[value.role]} label source columns`}
            className={input}
            placeholder="for example: meanGrade, grades"
            value={sourcesText}
            onChange={(e) => {
              setSourcesText(e.target.value);
              change({ ...value, label_source_columns: parseColumns(e.target.value) });
            }}
          />
          <span className="mt-0.5 block text-slate-500 dark:text-slate-400">
            The shortcut audit (D-3) does not score these columns, because the label was defined from them; it lists them as excluded.
          </span>
        </label>
      )}
      <div className="mt-2">
        {values ? (
          <LabelMappingEditor
            values={values}
            mapping={value.label_mapping}
            calibration={value.role === 'calibration_negatives'}
            onChange={(label_mapping) => change({ ...value, label_mapping })}
          />
        ) : (
          <label className="text-xs text-slate-600 dark:text-slate-300">
            Label mapping (value=positive, value=negative, value=excluded)
            <input
              aria-label={`${ROLE_WORDS[value.role]} label mapping`}
              className={input}
              value={mappingText}
              onChange={(e) => {
                setMappingText(e.target.value);
                change({ ...value, label_mapping: parseMapping(e.target.value) });
              }}
            />
          </label>
        )}
      </div>
      {value.role === 'calibration_negatives' && (
        <label className="mt-2 block text-xs text-slate-600 dark:text-slate-300">
          Basis of the calibration negatives
          <select
            aria-label="Calibration negatives basis"
            className={input}
            value={value.negatives_basis?.kind ?? 'assumed_negative'}
            onChange={(e) => change({ ...value, negatives_basis: basisFor(e.target.value as NegativesBasis['kind'], value) })}
          >
            <option value="assumed_negative">Assumed negative (an unlabeled corpus)</option>
            <option value="labeler_filtered">Kept because a labeler scored them negative</option>
            <option value="human_labelled">Labelled negative by people</option>
          </select>
        </label>
      )}
      {value.negatives_basis?.kind === 'human_labelled' && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2" data-testid="human-basis">
          <p className="sm:col-span-2 text-xs text-slate-500 dark:text-slate-400">
            Negatives are the rows whose {value.label_column || 'label column'} is{' '}
            {(value.negatives_basis.negative_values ?? []).join(', ') || 'a value the mapping sends to negative'}.
          </p>
          <label className="text-xs text-slate-600 dark:text-slate-300">
            Labelled by (who, and how many per row)
            <input
              aria-label="Labelled by"
              className={input}
              value={value.negatives_basis.labelled_by ?? ''}
              onChange={(e) => change({ ...value, negatives_basis: { ...value.negatives_basis!, labelled_by: e.target.value } })}
            />
          </label>
          <label className="text-xs text-slate-600 dark:text-slate-300">
            Rule (for example: mean grade ≤ 0.4)
            <input
              aria-label="Human labelling rule"
              className={input}
              value={value.negatives_basis.rule ?? ''}
              onChange={(e) => change({ ...value, negatives_basis: { ...value.negatives_basis!, rule: e.target.value } })}
            />
          </label>
        </div>
      )}
      {value.negatives_basis?.kind === 'labeler_filtered' && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="text-xs text-slate-600 dark:text-slate-300">
            Labeler identity hash
            <input
              aria-label="Labeler identity hash"
              className={input}
              value={value.negatives_basis.labeler_identity_hash ?? ''}
              onChange={(e) => change({ ...value, negatives_basis: { ...value.negatives_basis!, labeler_identity_hash: e.target.value } })}
            />
          </label>
          <label className="text-xs text-slate-600 dark:text-slate-300">
            Rule (for example: P ≤ 0.20)
            <input
              aria-label="Labeler rule"
              className={input}
              value={value.negatives_basis.rule ?? ''}
              onChange={(e) => change({ ...value, negatives_basis: { ...value.negatives_basis!, rule: e.target.value } })}
            />
          </label>
        </div>
      )}
      {onRemove && (
        <button type="button" className="mt-2 text-xs text-red-700 dark:text-red-300 underline" onClick={onRemove}>
          Remove this role
        </button>
      )}
    </fieldset>
  );
}

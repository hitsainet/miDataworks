// The column mapping for an imported calibration set (FR-006.3): human-label column and its numeric
// cut points (the excluded middle), optional per-rater column, group, strata and reference rows.
import type { CalibrationMapping } from '@/types/calibration';

export interface MappingDraft {
  labelColumn: string;
  positiveAtOrAbove: string;
  negativeAtOrBelow: string;
  ratingsColumn: string;
  ratingsFormat: 'digit_string' | 'list';
  groupColumn: string;
  strata: string;
  referenceColumn: string;
  referenceValue: string;
}

export const EMPTY_DRAFT: MappingDraft = {
  labelColumn: '', positiveAtOrAbove: '', negativeAtOrBelow: '', ratingsColumn: '', ratingsFormat: 'digit_string',
  groupColumn: '', strata: '', referenceColumn: '', referenceValue: '',
};

/** The mapping document for a draft, or null while it is incomplete. */
export function toMapping(d: MappingDraft): CalibrationMapping | null {
  const pos = Number(d.positiveAtOrAbove);
  const neg = Number(d.negativeAtOrBelow);
  if (!d.labelColumn || d.positiveAtOrAbove === '' || d.negativeAtOrBelow === '' || Number.isNaN(pos) || Number.isNaN(neg)) return null;
  const mapping: CalibrationMapping = {
    schema: 'dw.calibration-mapping/v1',
    human_label: { column: d.labelColumn, rule: 'numeric', positive_at_or_above: pos, negative_at_or_below: neg },
  };
  if (d.ratingsColumn) mapping.ratings = { column: d.ratingsColumn, format: d.ratingsFormat };
  if (d.groupColumn) mapping.group = { column: d.groupColumn };
  const strata = d.strata.split(',').map((s) => s.trim()).filter(Boolean);
  if (strata.length) mapping.strata = strata;
  if (d.referenceColumn) mapping.reference = { column: d.referenceColumn, value: d.referenceValue };
  return mapping;
}

const input = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function MappingForm({ draft, onChange }: { draft: MappingDraft; onChange: (d: MappingDraft) => void }) {
  const field = (key: keyof MappingDraft, label: string, placeholder = '') => (
    <label className="block text-xs">
      <span className="text-slate-600 dark:text-slate-300">{label}</span>
      <input className={input} value={draft[key]} placeholder={placeholder} onChange={(e) => onChange({ ...draft, [key]: e.target.value })} />
    </label>
  );
  return (
    <div className="grid grid-cols-2 gap-3" data-testid="mapping-form">
      {field('labelColumn', 'Human-label column', 'meanGrade')}
      <div className="grid grid-cols-2 gap-2">
        {field('positiveAtOrAbove', 'Positive at or above', '1.6')}
        {field('negativeAtOrBelow', 'Negative at or below', '0.4')}
      </div>
      {field('ratingsColumn', 'Per-rater column (optional)', 'grades')}
      <label className="block text-xs">
        <span className="text-slate-600 dark:text-slate-300">Ratings format</span>
        <select className={input} value={draft.ratingsFormat} onChange={(e) => onChange({ ...draft, ratingsFormat: e.target.value as MappingDraft['ratingsFormat'] })}>
          <option value="digit_string">Digit string (e.g. 33110)</option>
          <option value="list">List of integers</option>
        </select>
      </label>
      {field('groupColumn', 'Topic group column (optional)', 'pair_id')}
      {field('strata', 'Stratum columns, comma-separated (optional)', 'kind')}
      {field('referenceColumn', 'Reference rows: column (optional)', 'kind')}
      {field('referenceValue', 'Reference rows: value', 'original')}
    </div>
  );
}

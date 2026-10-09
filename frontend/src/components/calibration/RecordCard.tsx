// One calibration record (FR-006.33; mockup `Judges()`): four stat tiles, the verdict pill naming
// its rule and numbers, warnings, reliability and the checks card.
import type { CalibrationRecord } from '@/types/calibration';

import { ChecksCard } from './ChecksCard';
import { fmt3, fmtInt, fmtPct } from './format';
import { ReliabilityChart } from './ReliabilityChart';
import { StatTile } from './StatTile';
import { VerdictPill } from './VerdictPill';

function failedReason(record: CalibrationRecord, metric: string): string | null {
  const failed = record.checks.find((c) => c.metric_id === metric && c.result === 'fail');
  return failed ? failed.reason ?? failed.check_id : null;
}

export function RecordCard({ record }: { record: CalibrationRecord }) {
  const m = record.metrics;
  const model = String(record.labeler_identity.model_id ?? record.labeler_fingerprint.slice(0, 12));
  const confident = m.band_shares?.overall.at_or_above;
  return (
    <article data-testid="record-card" className="rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-5 space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-semibold">{model}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {record.score_kind === 'discrete' ? 'Score is discrete · ' : ''}Computed {new Date(record.created_at).toLocaleString()} by {record.created_by}
          </p>
        </div>
        <VerdictPill verdict={record.verdict} />
      </header>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          testId="tile-auroc"
          label="AUROC vs human labels"
          value={m.auroc ? fmt3(m.auroc.value) : undefined}
          sample={m.auroc ? `[${fmt3(m.auroc.ci_low)}, ${fmt3(m.auroc.ci_high)}] · ${fmtInt(m.auroc.n)} rows` : undefined}
          unavailable={m.auroc ? null : `Not available: ${m.reasons.auroc ?? 'no AUROC'}`}
          struck={failedReason(record, 'auroc')}
        />
        <StatTile
          testId="tile-ceiling"
          label="Held-out human rater"
          value={m.ceiling ? fmt3(m.ceiling.value) : undefined}
          sample={m.ceiling ? `[${fmt3(m.ceiling.ci_low)}, ${fmt3(m.ceiling.ci_high)}] · ${m.ceiling.draws} draws` : undefined}
          unavailable={m.ceiling ? null : m.reasons.ceiling ?? 'Not available'}
          struck={failedReason(record, 'ceiling')}
        />
        <StatTile
          testId="tile-paired"
          label="Same-group pair accuracy"
          value={m.paired ? fmtPct(m.paired.value) : undefined}
          sample={m.paired ? `${fmtInt(m.paired.pairs)} pairs, ${fmtInt(m.paired.groups)} groups` : undefined}
          unavailable={m.paired ? null : m.reasons.paired ?? 'Not available'}
          struck={failedReason(record, 'paired')}
        />
        <StatTile
          testId="tile-confident"
          label="Rows it is confident on"
          value={confident !== undefined ? fmtPct(confident) : undefined}
          sample={m.band_shares ? `at or above ${m.band_shares.threshold_positive} · ${fmtInt(m.band_shares.overall.n)} rows` : undefined}
          unavailable={m.band_shares ? null : `Not available: ${m.reasons.band_shares ?? 'no thresholds'}`}
        />
      </div>
      {m.reference_diagnostic && (
        <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="reference-diagnostic">
          Beats its reference: {fmtPct(m.reference_diagnostic.value)} of {fmtInt(m.reference_diagnostic.n_pos)} positive rows; control (negative rows):{' '}
          {fmtPct(m.reference_diagnostic.control)} of {fmtInt(m.reference_diagnostic.n_neg)}.
          {failedReason(record, 'reference_diagnostic') && ' Does not measure the concept: negatives beat their reference too.'}
        </p>
      )}
      {record.warnings.map((w, i) => (
        <p key={i} role="note" className="rounded border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-xs" data-testid="record-warning">
          {w.message ?? w.kind}
        </p>
      ))}
      <div className="grid gap-4 lg:grid-cols-2">
        <ReliabilityChart bins={m.reliability} reason={m.reasons.reliability} />
        <ChecksCard checks={record.checks} conformance={record.conformance} />
      </div>
    </article>
  );
}

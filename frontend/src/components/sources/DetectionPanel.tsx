// Each detection output with its reason, one line each (FR-001.19). An undetected result says what
// to do next instead of guessing a column (FR-001.20).
import type { Detection } from '@/types/sources';

const TRL_LABELS: Record<string, string> = {
  language_modeling: 'Language modeling',
  prompt_only: 'Prompt only',
  prompt_completion: 'Prompt and completion',
  preference: 'Preference',
  unpaired_preference: 'Unpaired preference',
  stepwise_supervision: 'Stepwise supervision',
  none: 'No TRL shape (plain rows)',
  undetected: 'Not detected',
};

export const UNDETECTED_ADVICE = 'Choose the text column before building a version.';

const label = (value: string) => TRL_LABELS[value] ?? value.replace(/_/g, ' ');

export function DetectionPanel({ detection }: { detection: Detection | null | undefined }) {
  if (!detection) {
    return <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="detection-none">No detection yet: it runs on the preview's sample rows and again on the stored data.</p>;
  }
  const undetected = detection.trl_type === 'undetected';
  const reasonFor = (output: string) => detection.reasons.filter((r) => r.output === output).map((r) => r.reason).join(' ');
  const rows: Array<[string, string, string]> = [
    ['Shape', label(detection.trl_type) + (detection.trl_format ? ` (${detection.trl_format})` : ''), reasonFor('trl_type')],
    ['Chat format', detection.chat_format.replace(/_/g, ' '), reasonFor('chat_format')],
    ['Text columns', detection.text_columns.join(', ') || 'none found', reasonFor('text_columns')],
    ['Label columns', detection.label_columns.join(', ') || 'none found', reasonFor('label_columns')],
    ['Suggested goal', detection.suggested_target, reasonFor('suggested_target')],
  ];
  return (
    <div data-testid="detection-panel" className="text-sm">
      {detection.overridden && <p className="text-xs mb-2 text-slate-500 dark:text-slate-400">Overridden by an operator; the original suggestion is kept below the source's annotations.</p>}
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[auto_1fr]">
        {rows.map(([name, value, reason]) => (
          <div key={name} className="contents">
            <dt className="text-slate-500 dark:text-slate-400">{name}</dt>
            <dd className="text-slate-800 dark:text-slate-200">
              <span className="font-medium">{value}</span>
              {reason && <span className="text-slate-500 dark:text-slate-400">: {reason}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {undetected && <p role="status" className="mt-2 text-amber-700 dark:text-amber-400">{UNDETECTED_ADVICE}</p>}
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400 font-mono">{detection.detector_version}</p>
    </div>
  );
}

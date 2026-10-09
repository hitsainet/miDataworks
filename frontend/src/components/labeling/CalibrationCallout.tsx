// The calibration status of this labeler identity, from feature 006 (FR-006.20; FPRD 005 4.2):
// green when it passes gate 2, amber when it fails or no record exists. Status is in words.
// Reads 006's real CalibrationStatus (status recorded/none_recorded + verdict); the earlier
// placeholder shape ('pass' | 'fail' | 'none') matched nothing 006 serves.
import { statusWords } from '@/components/calibration/format';
import type { CalibrationStatus } from '@/types/calibration';
import { navigate } from '@/utils/navigate';

export function CalibrationCallout({ status }: { status: CalibrationStatus | undefined }) {
  const { tone: value, text } = statusWords(status);
  const pass = value === 'pass';
  return (
    <div
      data-testid="calibration-callout"
      data-status={value}
      className={`rounded-lg border px-3 py-2 text-sm ${pass ? 'border-green-300 dark:border-green-500/40 bg-green-50 dark:bg-green-500/10' : 'border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10'}`}
    >
      {text}{' '}
      <button type="button" className="underline focus-visible:ring-2 focus-visible:ring-indigo-500 rounded" onClick={() => navigate('calibration')}>
        Open Calibration
      </button>
    </div>
  );
}

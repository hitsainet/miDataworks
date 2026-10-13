// A form's refusal, backend or local: the message verbatim, then each field problem on its own line.
import type { FormRefusal } from '@/api/refusal';

export function RefusalBox({ refusal, testId }: { refusal: FormRefusal | null; testId?: string }) {
  if (!refusal) return null;
  return (
    <div role="alert" data-testid={testId} className="rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
      {refusal.message}
      {refusal.problems.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs font-mono">
          {refusal.problems.map((p, i) => <li key={i}>{p}</li>)}
        </ul>
      )}
    </div>
  );
}

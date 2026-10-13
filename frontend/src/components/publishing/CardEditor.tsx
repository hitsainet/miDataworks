// The dataset card (FR-008.8): front matter and the record section are generated and locked; only
// the prose is the operator's. A prose edit can never remove a caveat or a digest.
import type { CardDraft } from '@/types/publishing';

export function CardEditor({ draft, prose, onProse }: { draft: CardDraft | null; prose: string; onProse: (value: string) => void }) {
  return (
    <div className="mb-4">
      <label htmlFor="card-prose" className="block text-sm font-medium mb-1">
        Dataset card
      </label>
      <div className="text-xs text-slate-500 dark:text-slate-400 mb-2">
        Written for you from the version&apos;s record. Edit the prose; the front matter and the record are regenerated on every publish.
      </div>
      <textarea
        id="card-prose"
        className="w-full h-28 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm font-mono focus-visible:ring-2 focus-visible:ring-indigo-500"
        value={prose}
        placeholder={draft?.prose ?? '# Title\n\nWhat the rows are and how to use them.'}
        onChange={(e) => onProse(e.target.value)}
      />
      {draft && (
        <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-100 dark:bg-slate-800/70 p-3 text-xs font-mono text-slate-500 dark:text-slate-400" data-testid="card-locked" aria-label="Generated front matter and record (read-only)">
          {JSON.stringify(draft.front_matter, null, 1)}
          {'\n\n'}
          {draft.record_markdown}
        </pre>
      )}
    </div>
  );
}

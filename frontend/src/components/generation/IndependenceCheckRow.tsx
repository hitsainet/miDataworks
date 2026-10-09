// Judge versus generator identities, side by side (FR-007.24 – 007.26). The warning says what to do.
import { Button } from '@/components/common/Button';
import type { IndependenceResult, Identity } from '@/types/generation';

function show(id: Identity | null): string {
  if (!id) return 'no judge configured';
  return `${id.model_id} · revision ${id.revision.slice(0, 12)} · steering ${id.set_hash === 'none' ? 'none' : id.set_hash.slice(0, 15)}`;
}

export function IndependenceCheckRow({ result, onCheck }: { result: IndependenceResult | null; onCheck: () => void }) {
  return (
    <div data-testid="independence-row" className="space-y-2">
      <Button variant="secondary" size="sm" onClick={onCheck}>Check judge independence</Button>
      {result && (
        <div className="grid sm:grid-cols-2 gap-2 text-xs">
          <div>
            <div className="text-slate-500 dark:text-slate-400">Generator</div>
            {result.generator_identities.map((g) => (
              <div key={`${g.model_id}${g.set_hash}`} className="font-mono text-cyan-600 dark:text-cyan-400">{show(g)}</div>
            ))}
          </div>
          <div>
            <div className="text-slate-500 dark:text-slate-400">Judge</div>
            <div className="font-mono">{show(result.judge_identity)}</div>
          </div>
          <div
            role={result.independent === false ? 'alert' : undefined}
            className={`sm:col-span-2 text-sm ${result.independent === false ? 'text-red-600 dark:text-red-400' : 'text-slate-600 dark:text-slate-300'}`}
          >
            {result.independent === false ? 'Not independent: ' : result.independent ? 'Independent: ' : 'Not checked: '}
            {result.message}
          </div>
        </div>
      )}
    </div>
  );
}

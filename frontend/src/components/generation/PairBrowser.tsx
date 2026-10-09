// The pairs a steered run kept: prompt, both answers, both reported steerings (cyan), the chosen side.
import type { GenerationPair } from '@/types/generation';

export function PairBrowser({ pairs }: { pairs: GenerationPair[] }) {
  if (pairs.length === 0) return <p className="text-sm text-slate-500 dark:text-slate-400">No pairs kept yet.</p>;
  return (
    <ul className="space-y-2" data-testid="pair-browser">
      {pairs.map((p) => (
        <li key={`${p.prompt_row_key}-${p.pair_index}`} className="rounded-lg border border-slate-200 dark:border-slate-700 p-2 text-sm">
          <div className="text-xs text-slate-500 dark:text-slate-400">{p.prompt} · seed {p.shared_seed}</div>
          <div className="grid sm:grid-cols-2 gap-2 mt-1">
            {(['a', 'b'] as const).map((side) => (
              <div key={side}>
                <div className="text-xs">
                  Side {side.toUpperCase()} {p.chosen_side === side ? '(chosen)' : '(rejected)'} ·{' '}
                  <span className="font-mono text-cyan-600 dark:text-cyan-400">{side === 'a' ? p.steering_a : p.steering_b}</span>
                </div>
                <div className="whitespace-pre-wrap">{side === 'a' ? p.text_a : p.text_b}</div>
              </div>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

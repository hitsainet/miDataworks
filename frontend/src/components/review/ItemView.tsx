// One review item: text (rendered as TEXT, never HTML — external candidates are untrusted), the
// model's label, rationale and probability unless the queue hides them, and the decision history.
import type { ReviewItem, ReviewQueue } from '@/types/review';

export function ItemView({ item, queue }: { item: ReviewItem; queue: ReviewQueue }) {
  const snap = item.model_snapshot;
  const provenance = item.payload?.provenance as { run_id?: string; step?: number } | undefined;
  return (
    <div className="space-y-3" data-testid="item-view">
      <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
        <span>Item {item.position + 1}{item.stratum ? ` · stratum ${item.stratum}` : ''}</span>
        {queue.origin_app === 'miforge' && (
          <span className="rounded bg-orange-100 dark:bg-orange-500/15 px-1.5 text-orange-700 dark:text-orange-300" data-testid="miforge-chip">
            miForge{provenance?.run_id ? ` · run ${provenance.run_id}` : ''}{provenance?.step !== undefined ? ` · step ${provenance.step}` : ''}
          </span>
        )}
      </div>
      {item.text && Object.entries(item.text).map(([col, value]) => (
        <p key={col} className="whitespace-pre-wrap rounded-lg bg-slate-50 dark:bg-slate-800/60 p-3 text-sm" data-testid="item-text">{value}</p>
      ))}
      {item.payload && (
        <div className="space-y-2 text-sm">
          <p className="whitespace-pre-wrap rounded bg-slate-50 dark:bg-slate-800/60 p-2"><span className="text-xs text-slate-500">Prompt</span><br />{item.payload.prompt}</p>
          <p className="whitespace-pre-wrap rounded bg-slate-50 dark:bg-slate-800/60 p-2" data-testid="item-completion"><span className="text-xs text-slate-500">Completion</span><br />{item.payload.completion}</p>
        </div>
      )}
      {item.model_output_hidden ? (
        <p role="note" className="text-xs text-slate-500 dark:text-slate-400" data-testid="model-hidden">
          Model output hidden: you are labeling for a calibration set, and seeing the model's answer would anchor yours.
        </p>
      ) : snap ? (
        <p className="text-xs tabular-nums" data-testid="model-snapshot">
          Model label: <strong>{snap.label ?? snap.outcome ?? 'none'}</strong>
          {typeof snap.probability === 'number' ? ` · P ${snap.probability.toFixed(3)}` : ''}
          {snap.label_run_id ? ` · run ${snap.label_run_id}` : ''}
          {snap.rationale ? ` · ${snap.rationale}` : ''}
        </p>
      ) : null}
    </div>
  );
}

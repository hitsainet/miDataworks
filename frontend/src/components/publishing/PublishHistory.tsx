// Published and exported (FR-008.55): repository, short commit, visibility and the hash pill.
// miStudio probe-dataset publishes are emerald, miForge entries orange (R-03.59).
import { CheckCircle2, ShieldCheck, Upload, XCircle } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { WhoBadge } from '@/components/common/WhoBadge';
import type { ExportRecord, PublishRecord } from '@/types/publishing';

function splitSummary(p: PublishRecord): string {
  const splits = p.files.filter((f) => f.role === 'split');
  return splits.length ? splits.map((f) => `${f.split}`).join(' · ') : p.kind === 'card_only' ? 'card only' : 'version publish';
}

export function PublishHistory({ publishes, exports, onReverify }: { publishes: PublishRecord[]; exports: ExportRecord[]; onReverify: (id: string) => void }) {
  if (!publishes.length && !exports.length) {
    return <div className="text-sm text-slate-500 dark:text-slate-400">Nothing published or exported yet.</div>;
  }
  return (
    <div data-testid="publish-history">
      {publishes.map((p) => {
        const verified = p.status === 'published';
        const tone = p.send_id ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-900 dark:text-slate-100';
        return (
          <Card key={p.id} className="mb-3 py-4" padding="sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="rounded-lg bg-indigo-500/10 p-2 text-indigo-500"><Upload className="w-4 h-4" aria-hidden /></span>
                <div className="min-w-0">
                  <div className={`font-semibold font-mono text-sm break-all ${tone}`}>{p.repo_id}</div>
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    {p.status.replace('_', ' ')} · {splitSummary(p)} · by <WhoBadge who={p.started_by} />
                  </div>
                  {p.error && <div className="text-xs text-red-700 dark:text-red-300">{p.error.message}</div>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {p.commit && <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{p.commit.slice(0, 8)}</span>}
                {p.visibility_after && <span className="rounded-full border border-slate-300 dark:border-slate-600 px-2 py-0.5 text-xs">{p.visibility_after === 'public' ? 'Public' : 'Private'}</span>}
                {verified ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300"><CheckCircle2 className="w-3 h-3" aria-hidden />Hashes match</span>
                ) : p.status === 'verification_failed' ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-xs text-red-700 dark:text-red-300"><XCircle className="w-3 h-3" aria-hidden />Hashes differ</span>
                ) : null}
                {p.commit && (
                  <Button variant="ghost" size="sm" leftIcon={<ShieldCheck className="w-3 h-3" />} onClick={() => onReverify(p.id)}>
                    Re-verify
                  </Button>
                )}
              </div>
            </div>
          </Card>
        );
      })}
      {exports.map((e) => (
        <Card key={e.id} className="mb-3 py-4" padding="sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className={`font-mono text-sm ${e.target === 'miforge_set' ? 'text-orange-600 dark:text-orange-400' : ''}`}>
              {e.target === 'trl' ? `TRL ${String(e.params.trl_type).toUpperCase()}` : `miForge ${String(e.params.miforge_set_kind)}`} · {e.status}
            </div>
            <div className="text-xs text-slate-500 dark:text-slate-400">{(e.files ?? []).filter((f) => f.role === 'data').length} file(s){e.error ? ` · ${e.error.message}` : ''}</div>
          </div>
        </Card>
      ))}
    </div>
  );
}

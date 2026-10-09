// Export (FR-008.25, 008.41): TRL-ready files (TRL is Hugging Face's Transformer Reinforcement
// Learning library) and miForge sets. Reward bundles arrive with milestone M4.
import { Download } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import type { ExportRequest } from '@/types/publishing';

const TRL_TYPES = ['sft', 'dpo', 'kto', 'grpo_prompt', 'prm'];
const SETS = ['prompt_set', 'corpus', 'preference_pairs', 'test_set', 'retention_set'];

export function ExportPanel({ versionId, labelColumn, onExport }: { versionId: string; labelColumn: string; onExport: (r: ExportRequest) => void }) {
  const [tab, setTab] = useState<'trl' | 'miforge_set'>('trl');
  const [kind, setKind] = useState('dpo');
  const [setKindValue, setSetKind] = useState('prompt_set');
  const [format, setFormat] = useState<'parquet' | 'jsonl'>('parquet');
  const field = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  const label = tab === 'trl' ? `Export ${kind.replace('_prompt', '').toUpperCase()} files` : 'Export to miForge';
  return (
    <Card className="mb-6" data-testid="export-panel">
      <div className="font-semibold mb-1">Export</div>
      <div className="text-xs text-slate-500 dark:text-slate-400 mb-3">
        Files with exactly the columns the trainer reads, a row-key file beside each, and the handoff manifest. Every export is checked by the TRL format validator first; nothing is pushed to the Hub.
      </div>
      <div role="tablist" className="flex gap-2 mb-3">
        {(['trl', 'miforge_set'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`rounded px-3 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 ${tab === t ? 'bg-indigo-500 text-white' : 'border border-slate-300 dark:border-slate-600'}`} onClick={() => setTab(t)}>
            {t === 'trl' ? 'TRL files' : 'miForge set'}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {tab === 'trl' ? (
          <label className="text-sm">
            Type
            <select className={`${field} ml-2`} value={kind} onChange={(e) => setKind(e.target.value)}>
              {TRL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        ) : (
          <label className="text-sm">
            Set kind
            <select className={`${field} ml-2`} value={setKindValue} onChange={(e) => setSetKind(e.target.value)}>
              {SETS.map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
            </select>
          </label>
        )}
        <label className="text-sm">
          Format
          <select className={`${field} ml-2`} value={format} onChange={(e) => setFormat(e.target.value as 'parquet' | 'jsonl')}>
            <option value="parquet">Parquet</option>
            <option value="jsonl">JSON Lines</option>
          </select>
        </label>
        <Button
          variant="secondary"
          leftIcon={<Download className="w-4 h-4" />}
          disabled={!versionId}
          onClick={() =>
            onExport(
              tab === 'trl'
                ? { target: 'trl', version_id: versionId, trl_type: kind, format, label_column: labelColumn || null }
                : { target: 'miforge_set', version_id: versionId, miforge_set_kind: setKindValue, format, label_column: labelColumn || null },
            )
          }
        >
          {label}
        </Button>
      </div>
    </Card>
  );
}

// Model terms (FR-008.60): whether a labeling or generating model's terms permit training on its
// outputs. Operator only and append-only; check C-4 reads the latest note.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import type { ModelTerms } from '@/types/publishing';

export function ModelTermsDialog({ terms, onLookup, onRecord }: { terms: ModelTerms | null; onLookup: (id: string) => void; onRecord: (id: string, value: 'permits' | 'forbids', text: string) => void }) {
  const [modelId, setModelId] = useState('');
  const [value, setValue] = useState<'permits' | 'forbids'>('permits');
  const [text, setText] = useState('');
  const field = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
  return (
    <Card className="mb-6" data-testid="model-terms">
      <div className="font-semibold mb-1">Model terms</div>
      <div className="text-xs text-slate-500 dark:text-slate-400 mb-3">Record whether a model&apos;s terms permit training on its outputs. Notes are kept, never edited; the latest one decides check C-4.</div>
      <div className="flex flex-wrap gap-2 items-end">
        <label className="text-sm">
          Model ID
          <input className={`${field} ml-2 font-mono`} value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder="org/model" />
        </label>
        <Button variant="secondary" disabled={!modelId} onClick={() => onLookup(modelId)}>Show notes</Button>
      </div>
      {terms && terms.model_id === modelId && (
        <div className="mt-3 text-sm">
          <div>Latest: {terms.latest ?? 'no note recorded'}</div>
          <ul className="text-xs text-slate-500 dark:text-slate-400">
            {terms.notes.map((n) => <li key={n.id}>{n.training_on_outputs} · {n.text} · {n.noted_by}</li>)}
          </ul>
          <div className="flex flex-wrap gap-2 items-end mt-2">
            <select className={field} value={value} onChange={(e) => setValue(e.target.value as 'permits' | 'forbids')} aria-label="Training on outputs">
              <option value="permits">Permits training on outputs</option>
              <option value="forbids">Forbids training on outputs</option>
            </select>
            <input className={`${field} flex-1`} value={text} onChange={(e) => setText(e.target.value)} placeholder="What the terms say, and where" aria-label="Note" />
            <Button disabled={!text} onClick={() => { onRecord(modelId, value, text); setText(''); }}>Record note</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

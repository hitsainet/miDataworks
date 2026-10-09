// "Try on a sample" (FR-007.44): up to 5 prompts now, no lease; each answer shows miLLM's reported
// steering in cyan and whether it matched what was asked.
import { useState } from 'react';

import { Button } from '@/components/common/Button';
import { useGenerationStore } from '@/stores/generationStore';
import type { SteeringSetting } from '@/types/generation';

export function TryOnSamplePanel({ setting, templateId }: { setting: SteeringSetting; templateId: string | null }) {
  const [text, setText] = useState('');
  const preview = useGenerationStore((s) => s.preview);
  const runPreview = useGenerationStore((s) => s.runPreview);
  const prompts = text.split('\n').map((p) => p.trim()).filter(Boolean).slice(0, 5);
  return (
    <div className="space-y-2" data-testid="try-on-sample">
      <label className="block text-xs text-slate-500 dark:text-slate-400" htmlFor="preview-prompts">Prompts to try (one per line, up to 5)</label>
      <textarea id="preview-prompts" className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      <Button variant="secondary" size="sm" disabled={prompts.length === 0} onClick={() => void runPreview(prompts, setting, templateId)}>
        Try {prompts.length || ''} prompt{prompts.length === 1 ? '' : 's'}
      </Button>
      {preview && (
        <ul className="space-y-2">
          {preview.items.map((item) => (
            <li key={item.prompt} className="rounded-lg border border-slate-200 dark:border-slate-700 p-2 text-sm">
              <div className="text-slate-500 dark:text-slate-400 text-xs">{item.prompt}</div>
              <div className="whitespace-pre-wrap">{item.text ?? item.error ?? '—'}</div>
              <div className="text-xs mt-1">
                <span className="text-cyan-600 dark:text-cyan-400 font-mono">{item.reported_steering ?? 'steering not reported'}</span>
                <span className="text-slate-500 dark:text-slate-400"> · check: {item.steering_check}{item.check_reasons.length ? ` (${item.check_reasons.join(', ')})` : ''}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

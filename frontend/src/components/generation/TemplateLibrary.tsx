// Generation templates (FR-007.7): the library, a new-template form and Clone. A template is saved
// once and never edited: a change is a clone, which keeps the name and takes the next version.
// After a save the placeholders the backend found are shown first, because a misspelled
// placeholder is not refused at save time: it reads as an empty string in a preview.
import { Copy, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

import { localRefusal } from '@/api/refusal';
import type { FormRefusal } from '@/api/refusal';
import { Button } from '@/components/common/Button';
import { RefusalBox } from '@/components/common/RefusalBox';
import { useGenerationStore } from '@/stores/generationStore';
import type { GenerationTemplate, TemplateBody } from '@/types/generation';

const FIELD = 'w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
const LABEL = 'block text-xs text-slate-500 dark:text-slate-400';
const HINT = 'block mt-1 text-xs text-slate-500 dark:text-slate-400';
export const NAME_PATTERN = /^[a-z0-9][a-z0-9_./-]{0,119}$/;
export const PROMPT_HINT =
  '{column} reads that column of the seed row. {prompt} is the run’s prompt column. Write {{ and }} for literal braces.';

type Mode = { kind: 'list' } | { kind: 'new' } | { kind: 'clone'; source: GenerationTemplate };

interface Draft {
  name: string;
  kind: 'respond' | 'expand';
  description: string;
  system: string;
  prompt: string;
  temperature: string;
  topP: string;
  maxTokens: string;
  structured: 'none' | 'json_schema';
  schema: string;
}

const EMPTY: Draft = {
  name: '', kind: 'respond', description: '', system: '', prompt: '', temperature: '0.8', topP: '1', maxTokens: '512', structured: 'none', schema: '',
};

function draftFrom(t: GenerationTemplate): Draft {
  const body = t.body as Partial<TemplateBody>;
  return {
    name: t.name,
    kind: t.kind,
    description: t.description ?? '',
    system: body.system ?? '',
    prompt: body.prompt ?? '',
    temperature: String(body.sampling?.temperature ?? 0.8),
    topP: String(body.sampling?.top_p ?? 1),
    maxTokens: String(body.sampling?.max_tokens ?? 512),
    structured: body.structured_output === 'json_schema' ? 'json_schema' : 'none',
    schema: body.json_schema ? JSON.stringify(body.json_schema, null, 2) : '',
  };
}

/** The body to send, or a refusal the form shows without calling the API. */
export function bodyFromDraft(d: Draft): TemplateBody | FormRefusal {
  if (d.prompt.trim() === '') return localRefusal('Write a prompt.');
  const temperature = Number(d.temperature);
  const topP = Number(d.topP);
  const maxTokens = Number(d.maxTokens);
  if (d.temperature.trim() === '' || !Number.isFinite(temperature) || temperature < 0 || temperature > 2) return localRefusal('Set the temperature between 0 and 2.');
  if (d.topP.trim() === '' || !Number.isFinite(topP) || topP <= 0 || topP > 1) return localRefusal('Set top-p above 0 and at most 1.');
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 8192) return localRefusal('Set max tokens to a whole number from 1 to 8,192.');
  const body: TemplateBody = {
    prompt: d.prompt,
    system: d.system.trim() === '' ? null : d.system,
    sampling: { temperature, top_p: topP, max_tokens: maxTokens },
    structured_output: d.structured,
  };
  if (d.structured === 'json_schema') {
    let schema: unknown;
    try {
      schema = JSON.parse(d.schema);
    } catch (e) {
      return localRefusal(`The JSON schema is not valid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
    if (schema === null || typeof schema !== 'object' || Array.isArray(schema) || Object.keys(schema).length === 0) {
      return localRefusal('The JSON schema must be a JSON object with at least one key.');
    }
    body.json_schema = schema as Record<string, unknown>;
  }
  return body;
}

const isRefusal = (x: TemplateBody | FormRefusal): x is FormRefusal => 'problems' in x;

function TemplateForm({ source, onCancel, onSaved }: { source: GenerationTemplate | null; onCancel: () => void; onSaved: (t: GenerationTemplate) => void }) {
  const createTemplate = useGenerationStore((s) => s.createTemplate);
  const cloneTemplate = useGenerationStore((s) => s.cloneTemplate);
  const backendRefusal = useGenerationStore((s) => s.templateError);
  const [d, setD] = useState<Draft>(source ? draftFrom(source) : EMPTY);
  const [local, setLocal] = useState<FormRefusal | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const nameOk = source !== null || NAME_PATTERN.test(d.name);

  const save = async () => {
    const body = bodyFromDraft(d);
    if (isRefusal(body)) {
      setLocal(body);
      return;
    }
    setLocal(null);
    setBusy(true);
    // A clone sends the description as edited (an emptied one is cleared); a new template sends none.
    const saved = source
      ? await cloneTemplate(source.id, { body, description: d.description })
      : await createTemplate({ name: d.name, kind: d.kind, description: d.description.trim() === '' ? null : d.description, body });
    setBusy(false);
    if (saved) onSaved(saved);
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 space-y-3" data-testid="template-form">
      <h2 className="font-semibold">{source ? `Clone ${source.ref}` : 'New template'}</h2>
      {source && <p className="text-xs text-slate-500 dark:text-slate-400">The clone keeps the name {source.name} and the kind {source.kind}, and is saved as the next version. The source does not change.</p>}
      <div className="grid sm:grid-cols-2 gap-3">
        <label className={LABEL}>Name
          <input aria-label="Template name" className={`${FIELD} font-mono`} value={d.name} disabled={source !== null} onChange={(e) => set({ name: e.target.value })} />
          {!source && <span className={HINT}>Lowercase letters, digits and . _ / -, starting with a letter or digit.</span>}
          {!source && d.name !== '' && !nameOk && <span role="alert" className="block mt-1 text-xs text-red-600 dark:text-red-400">This name has a character the backend refuses.</span>}
        </label>
        <label className={LABEL}>Kind
          <select aria-label="Template kind" className={FIELD} value={d.kind} disabled={source !== null} onChange={(e) => set({ kind: e.target.value as Draft['kind'] })}>
            <option value="respond">Respond: answers each prompt</option>
            <option value="expand">Expand: writes new prompts from each seed row</option>
          </select>
        </label>
      </div>
      <label className={LABEL}>Description (optional)
        <textarea aria-label="Template description" rows={2} className={FIELD} value={d.description} onChange={(e) => set({ description: e.target.value })} />
      </label>
      <label className={LABEL}>System message (optional)
        <textarea aria-label="System message" rows={3} className={`${FIELD} font-mono`} value={d.system} onChange={(e) => set({ system: e.target.value })} />
      </label>
      <label className={LABEL}>Prompt
        <textarea aria-label="Prompt" rows={8} className={`${FIELD} font-mono`} value={d.prompt} onChange={(e) => set({ prompt: e.target.value })} />
        <span className={HINT}>{PROMPT_HINT}</span>
      </label>
      <div className="grid sm:grid-cols-3 gap-3">
        <label className={LABEL}>Temperature (0 to 2)
          <input aria-label="Temperature" inputMode="decimal" className={`${FIELD} font-mono`} value={d.temperature} onChange={(e) => set({ temperature: e.target.value })} />
        </label>
        <label className={LABEL}>Top-p (above 0, at most 1)
          <input aria-label="Top-p" inputMode="decimal" className={`${FIELD} font-mono`} value={d.topP} onChange={(e) => set({ topP: e.target.value })} />
        </label>
        <label className={LABEL}>Max tokens per answer (1 to 8,192)
          <input aria-label="Max tokens" inputMode="numeric" className={`${FIELD} font-mono`} value={d.maxTokens} onChange={(e) => set({ maxTokens: e.target.value })} />
        </label>
      </div>
      <label className={LABEL}>Structured output
        <select aria-label="Structured output" className={FIELD} value={d.structured} onChange={(e) => set({ structured: e.target.value as Draft['structured'] })}>
          <option value="none">None: free text</option>
          <option value="json_schema">JSON schema: the answer must match a schema</option>
        </select>
      </label>
      {d.structured === 'json_schema' && (
        <label className={LABEL}>JSON schema
          <textarea aria-label="JSON schema" rows={6} className={`${FIELD} font-mono`} value={d.schema} onChange={(e) => set({ schema: e.target.value })} />
          <span className={HINT}>Checked as JSON here before it is sent.</span>
        </label>
      )}
      <RefusalBox refusal={local ?? backendRefusal} testId="template-refusal" />
      <div className="flex gap-2">
        <Button size="sm" loading={busy} disabled={!nameOk || d.name === ''} onClick={() => void save()}>{source ? 'Save the clone' : 'Save template'}</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** What the backend saved: the ref first, then the placeholders it found, for the operator to check. */
export function SavedTemplate({ template }: { template: GenerationTemplate }) {
  return (
    <div role="status" className="rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50 dark:bg-indigo-500/10 px-4 py-3 text-sm" data-testid="saved-template">
      <div>Saved as <span className="font-mono font-semibold">{template.ref}</span>.</div>
      <div className="mt-2 font-medium">Placeholders found</div>
      {template.placeholders.length ? (
        <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="saved-placeholders">
          {template.placeholders.map((p) => (
            <li key={p} className="rounded bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 px-1.5 py-0.5 font-mono text-xs">{`{${p}}`}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1" data-testid="saved-placeholders">None. Every request sends the same text.</p>
      )}
      <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
        Check this list is exactly what you meant. A misspelled name is not refused here: it reads as an empty string in a preview. A run’s plan refuses any name that is not {'{prompt}'} or a column of the input version.
      </p>
    </div>
  );
}

export function TemplateLibrary() {
  const templates = useGenerationStore((s) => s.templates);
  const loadTemplates = useGenerationStore((s) => s.loadTemplates);
  const [mode, setMode] = useState<Mode>({ kind: 'list' });
  const [saved, setSaved] = useState<GenerationTemplate | null>(null);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const open = (next: Mode) => {
    useGenerationStore.setState({ templateError: null });
    setSaved(null);
    setMode(next);
  };

  return (
    <div className="space-y-4" data-testid="template-library">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold">Templates</h2>
        <Button size="sm" leftIcon={<Plus className="w-4 h-4" />} onClick={() => open({ kind: 'new' })}>New template</Button>
      </div>
      {saved && <SavedTemplate template={saved} />}
      {mode.kind !== 'list' && (
        <TemplateForm
          key={mode.kind === 'clone' ? mode.source.id : 'new'}
          source={mode.kind === 'clone' ? mode.source : null}
          onCancel={() => open({ kind: 'list' })}
          onSaved={(t) => { setMode({ kind: 'list' }); setSaved(t); }}
        />
      )}
      <ul className="space-y-2" data-testid="template-list">
        {templates.map((t) => (
          <li key={t.id} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <span className="font-mono">{t.ref}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400"> · {t.kind}{t.builtin ? ' · built in' : ''}{t.used ? ' · used, fixed' : ''}</span>
              </div>
              <Button size="sm" variant="secondary" leftIcon={<Copy className="w-3.5 h-3.5" />} aria-label={`Clone ${t.ref}`} onClick={() => open({ kind: 'clone', source: t })}>Clone</Button>
            </div>
            {t.description && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{t.description}</p>}
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Placeholders: {t.placeholders.length ? <span className="font-mono">{t.placeholders.map((p) => `{${p}}`).join(' ')}</span> : 'none'}
            </p>
          </li>
        ))}
        {templates.length === 0 && <li className="text-sm text-slate-500 dark:text-slate-400">No templates yet.</li>}
      </ul>
    </div>
  );
}

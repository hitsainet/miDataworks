// The judge's rubrics (FR-005.16, FR-005.17): the library, a new-rubric form, import from a
// `midataworks.rubric/v1` file, export to one, and Clone. A rubric is never edited in place: saving
// a name that exists, or cloning, makes the next version.
import { Copy, Download, Plus, Trash2, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { localRefusal } from '@/api/refusal';
import type { FormRefusal } from '@/api/refusal';
import { Button } from '@/components/common/Button';
import { RefusalBox } from '@/components/common/RefusalBox';
import { useLabelingStore } from '@/stores/labelingStore';
import type { Rubric, RubricBody, RubricParser, RubricStyle } from '@/types/labeling';

const FIELD = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500';
const LABEL = 'block text-xs text-slate-500 dark:text-slate-400';
const HINT = 'block mt-1 text-xs text-slate-500 dark:text-slate-400';
export const RUBRIC_NAME_PATTERN = /^[a-z0-9][a-z0-9_./-]*$/;
export const MESSAGE_HINT =
  '{field} reads that input field of the row and {question} the run’s question. A pairwise rubric also reads {a} and {b}. Write {{ and }} for literal braces.';

type Role = RubricBody['messages'][number]['role'];
interface Draft {
  name: string;
  style: RubricStyle;
  messages: Array<{ role: Role; content: string }>;
  inputFields: string;
  axes: string;
  parser: RubricParser;
  verdicts: string;
  schema: string;
  pairA: string;
  pairB: string;
  swap: string;
}

const EMPTY: Draft = {
  name: '', style: 'pointwise', messages: [{ role: 'system', content: '' }, { role: 'user', content: '' }], inputFields: 'text', axes: '',
  parser: 'verdict_line_v1', verdicts: '', schema: '', pairA: '', pairB: '', swap: '',
};

const list = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean);

function draftFrom(r: Rubric): Draft {
  const b = r.body as Partial<RubricBody>;
  return {
    name: r.name,
    style: (b.style ?? 'pointwise') as RubricStyle,
    messages: (b.messages ?? []).map((m) => ({ role: m.role, content: m.content })),
    inputFields: (b.input_fields ?? []).join(', '),
    axes: (b.axes ?? []).join(', '),
    parser: (b.parser ?? 'verdict_line_v1') as RubricParser,
    verdicts: (b.allowed_verdicts ?? []).join(', '),
    schema: b.json_schema ? JSON.stringify(b.json_schema, null, 2) : '',
    pairA: b.pair_fields?.[0] ?? '',
    pairB: b.pair_fields?.[1] ?? '',
    swap: b.swap_map ? Object.entries(b.swap_map).map(([k, v]) => `${k}=${v}`).join(', ') : '',
  };
}

/** The body to send, or a refusal the form shows without calling the API. */
export function rubricBodyFromDraft(d: Draft): RubricBody | FormRefusal {
  if (d.messages.length === 0) return localRefusal('Add at least one message.');
  if (d.messages.some((m) => m.content.trim() === '')) return localRefusal('Every message needs content. Remove the empty ones.');
  const inputFields = list(d.inputFields);
  if (inputFields.length === 0) return localRefusal('Name at least one input field.');
  const verdicts = list(d.verdicts);
  if (verdicts.length === 0) return localRefusal('Name at least one allowed verdict.');
  const body: RubricBody = { style: d.style, messages: d.messages, input_fields: inputFields, parser: d.parser, allowed_verdicts: verdicts };
  const axes = list(d.axes);
  if (axes.length) body.axes = axes;
  if (d.parser === 'json_v1') {
    let schema: unknown;
    try {
      schema = JSON.parse(d.schema);
    } catch (e) {
      return localRefusal(`The JSON schema is not valid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
    if (schema === null || typeof schema !== 'object' || Array.isArray(schema)) return localRefusal('The JSON schema must be a JSON object.');
    body.json_schema = schema as Record<string, unknown>;
  }
  if (d.style === 'pairwise') {
    if (d.pairA.trim() === '' || d.pairB.trim() === '') return localRefusal('A pairwise rubric needs both pair fields.');
    const entries = list(d.swap).map((pair) => pair.split('='));
    if (entries.length === 0 || entries.some((e) => e.length !== 2 || e[0].trim() === '' || e[1].trim() === '')) {
      return localRefusal('Write the swap map as verdict=verdict pairs, for example A=B, B=A, tie=tie.');
    }
    body.pair_fields = [d.pairA.trim(), d.pairB.trim()];
    body.swap_map = Object.fromEntries(entries.map(([k, v]) => [k.trim(), v.trim()]));
  }
  return body;
}

const isRefusal = (x: RubricBody | FormRefusal): x is FormRefusal => 'problems' in x;

function RubricForm({ source, onCancel, onSaved }: { source: Rubric | null; onCancel: () => void; onSaved: (r: Rubric) => void }) {
  const createRubric = useLabelingStore((s) => s.createRubric);
  const cloneRubric = useLabelingStore((s) => s.cloneRubric);
  const backendRefusal = useLabelingStore((s) => s.rubricError);
  const [d, setD] = useState<Draft>(source ? draftFrom(source) : EMPTY);
  const [local, setLocal] = useState<FormRefusal | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const setMessage = (i: number, patch: Partial<Draft['messages'][number]>) =>
    setD((prev) => ({ ...prev, messages: prev.messages.map((m, j) => (j === i ? { ...m, ...patch } : m)) }));
  const nameOk = source !== null || RUBRIC_NAME_PATTERN.test(d.name);

  const save = async () => {
    const body = rubricBodyFromDraft(d);
    if (isRefusal(body)) {
      setLocal(body);
      return;
    }
    setLocal(null);
    setBusy(true);
    const saved = source ? await cloneRubric(source.id, body) : await createRubric({ name: d.name, body });
    setBusy(false);
    if (saved) onSaved(saved);
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 space-y-3" data-testid="rubric-form">
      <h2 className="font-semibold">{source ? `Clone ${source.ref}` : 'New rubric'}</h2>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {source
          ? `The clone keeps the name ${source.name} and is saved as the next version. The source does not change.`
          : 'Saving a name that already exists saves its next version.'}
      </p>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className={LABEL}>Name
          <input aria-label="Rubric name" className={`${FIELD} font-mono`} value={d.name} disabled={source !== null} onChange={(e) => set({ name: e.target.value })} />
          {!source && <span className={HINT}>Lowercase letters, digits and . _ / -, starting with a letter or digit.</span>}
          {!source && d.name !== '' && !nameOk && <span role="alert" className="block mt-1 text-xs text-red-600 dark:text-red-400">This name has a character the backend refuses.</span>}
        </label>
        <label className={LABEL}>Style
          <select aria-label="Rubric style" className={FIELD} value={d.style} onChange={(e) => set({ style: e.target.value as RubricStyle })}>
            <option value="pointwise">Pointwise: one row, one verdict</option>
            <option value="binary">Binary: one row, yes or no</option>
            <option value="pairwise">Pairwise: which of two answers is better</option>
            <option value="stepwise">Stepwise: a verdict per step</option>
          </select>
        </label>
      </div>
      <fieldset className="space-y-2">
        <legend className={LABEL}>Messages sent to the judge, in order</legend>
        {d.messages.map((m, i) => (
          <div key={i} className="grid grid-cols-[7.5rem_minmax(0,1fr)_auto] gap-2 items-start" data-testid="rubric-message">
            <select aria-label={`Message ${i + 1} role`} className={FIELD} value={m.role} onChange={(e) => setMessage(i, { role: e.target.value as Role })}>
              <option value="system">system</option>
              <option value="user">user</option>
              <option value="assistant">assistant</option>
            </select>
            <textarea aria-label={`Message ${i + 1} content`} rows={3} className={`${FIELD} font-mono`} value={m.content} onChange={(e) => setMessage(i, { content: e.target.value })} />
            <Button size="sm" variant="ghost" aria-label={`Remove message ${i + 1}`} onClick={() => set({ messages: d.messages.filter((_, j) => j !== i) })}><Trash2 className="w-4 h-4" /></Button>
          </div>
        ))}
        <Button size="sm" variant="secondary" leftIcon={<Plus className="w-4 h-4" />} onClick={() => set({ messages: [...d.messages, { role: 'user', content: '' }] })}>Add message</Button>
        <span className={HINT}>{MESSAGE_HINT}</span>
      </fieldset>
      <div className="grid sm:grid-cols-2 gap-3">
        <label className={LABEL}>Input fields (comma-separated)
          <input aria-label="Input fields" className={`${FIELD} font-mono`} value={d.inputFields} onChange={(e) => set({ inputFields: e.target.value })} />
          <span className={HINT}>The names the messages read. A run maps each one to a column.</span>
        </label>
        <label className={LABEL}>Axes (optional, comma-separated)
          <input aria-label="Axes" className={`${FIELD} font-mono`} value={d.axes} onChange={(e) => set({ axes: e.target.value })} />
        </label>
        <label className={LABEL}>Answer format
          <select aria-label="Answer format" className={FIELD} value={d.parser} onChange={(e) => set({ parser: e.target.value as RubricParser })}>
            <option value="verdict_line_v1">A verdict line (verdict_line_v1)</option>
            <option value="json_v1">JSON matching a schema (json_v1)</option>
          </select>
        </label>
        <label className={LABEL}>Allowed verdicts (comma-separated)
          <input aria-label="Allowed verdicts" className={`${FIELD} font-mono`} value={d.verdicts} onChange={(e) => set({ verdicts: e.target.value })} />
        </label>
      </div>
      {d.parser === 'json_v1' && (
        <label className={LABEL}>JSON schema of the answer
          <textarea aria-label="Rubric JSON schema" rows={6} className={`${FIELD} font-mono`} value={d.schema} onChange={(e) => set({ schema: e.target.value })} />
          <span className={HINT}>Checked as JSON here before it is sent.</span>
        </label>
      )}
      {d.style === 'pairwise' && (
        <div className="grid sm:grid-cols-3 gap-3" data-testid="pairwise-fields">
          <label className={LABEL}>First answer field ({'{a}'})
            <input aria-label="Pair field A" className={`${FIELD} font-mono`} value={d.pairA} onChange={(e) => set({ pairA: e.target.value })} />
          </label>
          <label className={LABEL}>Second answer field ({'{b}'})
            <input aria-label="Pair field B" className={`${FIELD} font-mono`} value={d.pairB} onChange={(e) => set({ pairB: e.target.value })} />
          </label>
          <label className={LABEL}>Swap map
            <input aria-label="Swap map" placeholder="A=B, B=A, tie=tie" className={`${FIELD} font-mono`} value={d.swap} onChange={(e) => set({ swap: e.target.value })} />
            <span className={HINT}>How a verdict read with the answers swapped maps back. Keys must be allowed verdicts.</span>
          </label>
        </div>
      )}
      <RefusalBox refusal={local ?? backendRefusal} testId="rubric-refusal" />
      <div className="flex gap-2">
        <Button size="sm" loading={busy} disabled={!nameOk || d.name === ''} onClick={() => void save()}>{source ? 'Save the clone' : 'Save rubric'}</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

type Mode = { kind: 'list' } | { kind: 'new' } | { kind: 'clone'; source: Rubric };

/** `onSaved` lets a caller pick up the rubric just saved or imported (the Label step selects it). */
export function RubricLibrary({ onSaved }: { onSaved?: (r: Rubric) => void }) {
  const rubrics = useLabelingStore((s) => s.rubrics);
  const rubricError = useLabelingStore((s) => s.rubricError);
  const fetchRubrics = useLabelingStore((s) => s.fetchRubrics);
  const importRubric = useLabelingStore((s) => s.importRubric);
  const exportRubric = useLabelingStore((s) => s.exportRubric);
  const [mode, setMode] = useState<Mode>({ kind: 'list' });
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void fetchRubrics();
  }, [fetchRubrics]);

  const open = (next: Mode) => {
    useLabelingStore.setState({ rubricError: null });
    setNotice(null);
    setMode(next);
  };
  const done = (r: Rubric, verb: string) => {
    setMode({ kind: 'list' });
    setNotice(`${verb} ${r.ref} (${r.style}).`);
    onSaved?.(r);
  };

  return (
    <div className="space-y-4" data-testid="rubric-library">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Rubrics</h2>
        <div className="flex gap-2">
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" aria-label="Rubric file to import"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (!f) return;
              open({ kind: 'list' });
              const r = await importRubric(f);
              if (r) done(r, 'Imported');
            }} />
          <Button size="sm" variant="secondary" leftIcon={<Upload className="w-4 h-4" />} onClick={() => fileRef.current?.click()}>Import rubric</Button>
          <Button size="sm" leftIcon={<Plus className="w-4 h-4" />} onClick={() => open({ kind: 'new' })}>New rubric</Button>
        </div>
      </div>
      {notice && <div role="status" className="rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50 dark:bg-indigo-500/10 px-4 py-2 text-sm" data-testid="rubric-notice">{notice}</div>}
      {mode.kind === 'list' && <RefusalBox refusal={rubricError} testId="rubric-library-refusal" />}
      {mode.kind !== 'list' && (
        <RubricForm
          key={mode.kind === 'clone' ? mode.source.id : 'new'}
          source={mode.kind === 'clone' ? mode.source : null}
          onCancel={() => open({ kind: 'list' })}
          onSaved={(r) => done(r, 'Saved')}
        />
      )}
      <ul className="space-y-2" data-testid="rubric-list">
        {rubrics.map((r) => (
          <li key={r.id} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <span className="font-mono">{r.ref}</span>
              <span className="text-xs text-slate-500 dark:text-slate-400"> · {r.style}{r.used ? ' · used, fixed' : ''}</span>
              <div className="text-xs text-slate-500 dark:text-slate-400">
                Reads <span className="font-mono">{(r.body.input_fields ?? []).join(', ') || 'nothing'}</span> · verdicts <span className="font-mono">{(r.body.allowed_verdicts ?? []).join(', ')}</span>
              </div>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" leftIcon={<Copy className="w-3.5 h-3.5" />} aria-label={`Clone ${r.ref}`} onClick={() => open({ kind: 'clone', source: r })}>Clone</Button>
              <Button size="sm" variant="secondary" leftIcon={<Download className="w-3.5 h-3.5" />} aria-label={`Export ${r.ref}`} onClick={() => { setNotice(null); void exportRubric(r); }}>Export</Button>
            </div>
          </li>
        ))}
        {rubrics.length === 0 && <li className="text-sm text-slate-500 dark:text-slate-400">No rubrics yet. Write one with New rubric, or import a midataworks.rubric/v1 file.</li>}
      </ul>
    </div>
  );
}

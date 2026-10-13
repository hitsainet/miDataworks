// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/panels/SettingsPanel.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: encrypted keys shown masked, Fetch models beside each URL.
// Changed (ADR-011, handoff section 4a): endpoints are stored by ROLE — classifier, judge,
// generation, embeddings — never as one list (miStudio's list once let a delete button remove the
// labeling model); the judge has a Use setting; generation and embeddings inherit the judge unless
// set; the operator name (C5); other apps in their own colours; agent access is display-only (P-08).
import { useEffect, useState } from 'react';
import { CheckCircle2, Link2, Server } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { Spinner } from '@/components/common/Spinner';
import { ShortcutLevelControl } from '@/components/curation/ShortcutLevelControl';
import { PageHead } from '@/components/layout/PageHead';
import { RoleCapabilityLine } from '@/components/settings/RoleCapabilityLine';
import { RubricPicker } from '@/components/settings/RubricPicker';
import { TemplatePicker } from '@/components/settings/TemplatePicker';
import { AgentAccessCard } from '@/components/settings/AgentAccessCard';
import { APP_CHIP_CLASSES } from '@/config/brand';
import type { PanelDef } from '@/config/panels';
import { useHealthStore } from '@/stores/healthStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSourcesStore } from '@/stores/sourcesStore';
import { bytesToGb, formatBytes, gbToBytes } from '@/utils/format';
import type { EndpointRole, EndpointRoleWrite, Role } from '@/types/api';

export const PROTOCOL_LABELS: Record<string, string> = {
  openai_scoring: 'OpenAI-compatible scoring (logprobs + allowed tokens)',
  tei_classification: 'Hugging Face text classification (TEI /predict)',
  plugin: 'Plugin',
  openai_chat: 'OpenAI-compatible chat completions',
  openai_embeddings: 'OpenAI-compatible embeddings',
  tei_embeddings: 'Text Embeddings Inference (TEI)',
};

const ROLE_PROTOCOLS: Record<Role, string[]> = {
  classifier: ['openai_scoring', 'tei_classification', 'plugin'],
  judge: ['openai_chat'],
  generation: ['openai_chat'],
  embeddings: ['openai_embeddings', 'tei_embeddings'],
};

const ROLE_TEXT: Record<Role, { title: string; hint: string }> = {
  classifier: { title: 'Classifier endpoint', hint: 'Returns a probability over a fixed set of labels, one forward pass per row. Fast and calibratable.' },
  judge: { title: 'Judge endpoint', hint: 'Writes a verdict and a rationale against a rubric. Needed for pairwise and stepwise labels.' },
  generation: { title: 'Generation endpoint', hint: 'Writes synthetic rows. Uses the judge endpoint unless you set one here.' },
  embeddings: { title: 'Embeddings endpoint', hint: 'Embeds rows for near-duplicate checks. Uses the judge endpoint unless you set one here.' },
};

export const ONE_MODEL_CALLOUT =
  'miLLM serves one model at a time. With two roles on one miLLM, their runs cannot overlap: the second one waits.';

const field = 'w-full rounded-lg px-3 py-2.5 text-sm border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100';
const card = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';

function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="mb-4">
      <label htmlFor={htmlFor} className="block text-sm font-medium mb-1.5 text-slate-800 dark:text-slate-200">{label}</label>
      {children}
      {hint && <div className="text-xs mt-1 text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
}

function toWrite(role: EndpointRole): EndpointRoleWrite {
  return {
    protocol: role.protocol,
    base_url: role.base_url,
    model_id: role.model_id,
    api_key: null,
    inherit_from_judge: role.inherit_from_judge,
    use_mode: role.use_mode,
  };
}

function RoleCard({ role }: { role: EndpointRole }) {
  const saveRole = useSettingsStore((s) => s.saveRole);
  const fetchModels = useSettingsStore((s) => s.fetchModels);
  const listing = useSettingsStore((s) => s.models[role.role]);
  const [draft, setDraft] = useState<EndpointRoleWrite>(() => toWrite(role));
  const [key, setKey] = useState('');
  // A new role from the store replaces the draft. Adjusted during render rather than in an effect,
  // so no frame renders the old draft against the new role (React: "storing information from
  // previous renders"; react-hooks/set-state-in-effect).
  const [draftRole, setDraftRole] = useState(role);
  if (draftRole !== role) {
    setDraftRole(role);
    setDraft(toWrite(role));
  }
  const text = ROLE_TEXT[role.role];
  const inherits = (role.role === 'generation' || role.role === 'embeddings') && draft.inherit_from_judge;
  const delegated = role.role === 'judge' && draft.use_mode !== 'own';
  const id = (name: string) => `${role.role}-${name}`;

  return (
    <section className={card} aria-labelledby={id('title')} data-testid={`role-${role.role}`}>
      <h2 id={id('title')} className="font-semibold mb-1 text-slate-900 dark:text-slate-100">{text.title}</h2>
      <p className="text-xs mb-4 text-slate-500 dark:text-slate-400">{text.hint}</p>
      {role.role === 'judge' && (
        <Field label="Use" htmlFor={id('use')}>
          <select id={id('use')} className={field} value={draft.use_mode} onChange={(e) => setDraft({ ...draft, use_mode: e.target.value as EndpointRoleWrite['use_mode'] })}>
            <option value="own">Its own endpoint</option>
            <option value="same_as_classifier">Same as the classifier endpoint</option>
            <option value="none">None</option>
          </select>
        </Field>
      )}
      {(role.role === 'generation' || role.role === 'embeddings') && (
        <label className="flex items-center gap-2 mb-4 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={draft.inherit_from_judge} onChange={(e) => setDraft({ ...draft, inherit_from_judge: e.target.checked })} />
          Use the judge endpoint
        </label>
      )}
      {!inherits && !delegated && (
        <>
          <Field label="Protocol" htmlFor={id('protocol')}>
            <select id={id('protocol')} className={field} value={draft.protocol ?? ''} onChange={(e) => setDraft({ ...draft, protocol: e.target.value || null })}>
              <option value="">Choose a protocol</option>
              {ROLE_PROTOCOLS[role.role].map((p) => <option key={p} value={p}>{PROTOCOL_LABELS[p]}</option>)}
            </select>
          </Field>
          <Field label="Endpoint URL" htmlFor={id('url')} hint="Fetch models lists what the server serves.">
            <div className="flex gap-2">
              <input id={id('url')} className={`${field} font-mono`} value={draft.base_url ?? ''} placeholder="http://k8s-millm.hitsai.local/v1" onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
              <Button variant="secondary" onClick={() => void fetchModels(role.role, draft.base_url ?? undefined)}>Fetch models</Button>
            </div>
          </Field>
          <Field label="Model" htmlFor={id('model')} hint={listing ? `${listing.models.length} model${listing.models.length === 1 ? '' : 's'} from ${listing.url}` : undefined}>
            {listing ? (
              <select id={id('model')} className={field} value={draft.model_id ?? ''} onChange={(e) => setDraft({ ...draft, model_id: e.target.value || null })}>
                <option value="">Choose a model</option>
                {listing.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input id={id('model')} className={`${field} font-mono`} value={draft.model_id ?? ''} onChange={(e) => setDraft({ ...draft, model_id: e.target.value })} />
            )}
          </Field>
          <Field label="API key" htmlFor={id('key')} hint="Stored encrypted. Leave empty to keep the stored key; servers on your network may need none.">
            <input id={id('key')} type="password" className={field} value={key} placeholder={role.api_key ?? '(none)'} onChange={(e) => setKey(e.target.value)} />
          </Field>
        </>
      )}
      {role.role === 'classifier' && <TemplatePicker />}
      {role.role === 'judge' && <RubricPicker />}
      <RoleCapabilityLine role={role.role} />
      {role.role === 'judge' && (
        <p className="mb-4 text-xs rounded-lg p-3 bg-slate-100 dark:bg-slate-900 text-slate-600 dark:text-slate-400">{ONE_MODEL_CALLOUT}</p>
      )}
      <Button onClick={() => void saveRole(role.role, { ...draft, api_key: key ? key : null })}>
        Save the {role.role} endpoint
      </Button>
    </section>
  );
}

function OperatorNameCard() {
  const settings = useSettingsStore((s) => s.settings);
  const saveSetting = useSettingsStore((s) => s.saveSetting);
  const current = settings.find((s) => s.key === 'operator_name')?.value ?? '';
  const [name, setName] = useState(current);
  const [nameFrom, setNameFrom] = useState(current);
  if (nameFrom !== current) {
    setNameFrom(current);
    setName(current);
  }
  return (
    <section className={card} aria-labelledby="who-title" data-testid="operator-name">
      <h2 id="who-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">Your name</h2>
      <p className="text-xs mb-4 text-slate-500 dark:text-slate-400">Recorded as who started a run or decided a review. Work you start is refused until it is set.</p>
      <Field label="Name" htmlFor="operator-name-input">
        <input id="operator-name-input" className={field} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Button onClick={() => void saveSetting('operator_name', name)}>Save your name</Button>
    </section>
  );
}

/** Upload cap and import confirmation size (T-02): app settings with deployment defaults.
 * Entered in GB, any size above 0; the arrows step 1 GB. The setting itself stays in bytes. */
function StorageLimits() {
  const settings = useSettingsStore((s) => s.settings);
  const saveSetting = useSettingsStore((s) => s.saveSetting);
  const meta = useSourcesStore((s) => s.meta);
  const fetchSourcesMeta = useSourcesStore((s) => s.fetchSourcesMeta);
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    void fetchSourcesMeta();
  }, [fetchSourcesMeta]);
  const rows = [
    { key: 'upload_max_bytes', label: 'Largest upload, in GB', effective: meta?.limits.upload_max_bytes },
    { key: 'import_confirm_bytes', label: 'Imports above this size need a confirmation, in GB', effective: meta?.limits.import_confirm_bytes },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 mt-3" data-testid="storage-limits">
      {rows.map(({ key, label, effective }) => {
        const stored = settings.find((s) => s.key === key)?.value ?? '';
        const value = values[key] ?? bytesToGb(stored);
        const bytes = gbToBytes(value);
        const blank = value.trim() === '';
        // A cleared box returns a stored limit to the deployment default (the setting saved empty).
        const toSave = blank ? (stored ? '' : null) : bytes;
        const inForce = effective !== undefined ? `In force: ${formatBytes(effective)}${stored ? '' : ' (deployment default)'}.` : '';
        const entry = blank
          ? stored ? ' Saving clears it to the deployment default.' : ''
          : bytes === null ? ' Enter a size above 0 GB.' : ` Saves ${Number(bytes).toLocaleString('en-US')} bytes.`;
        return (
          <div key={key}>
            <Field label={label} hint={`${inForce}${entry}`.trim() || undefined} htmlFor={`${key}-input`}>
              <input
                id={`${key}-input`}
                type="number"
                min={0}
                step={1}
                inputMode="decimal"
                className={`${field} font-mono`}
                value={value}
                placeholder="deployment default"
                onChange={(e) => setValues({ ...values, [key]: e.target.value })}
              />
            </Field>
            <Button size="sm" variant="secondary" disabled={toSave === null} onClick={() => toSave !== null && void saveSetting(key, toSave).then((ok) => (ok ? fetchSourcesMeta(true) : undefined))}>Save the limit</Button>
          </div>
        );
      })}
    </div>
  );
}

function OtherAppsCard() {
  const settings = useSettingsStore((s) => s.settings);
  const saveSetting = useSettingsStore((s) => s.saveSetting);
  const health = useHealthStore((s) => s.health);
  const token = settings.find((s) => s.key === 'hf_token');
  const [value, setValue] = useState('');
  const app = (name: 'millm' | 'mistudio', label: string, Icon: typeof Server) => {
    const dep = health?.dependencies[name];
    const ok = dep?.ok ?? false;
    return (
      <div className="flex items-center gap-2 text-sm mb-2">
        <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full ${ok ? APP_CHIP_CLASSES[name] : 'bg-slate-500/10 text-slate-500 dark:text-slate-400'}`}>
          <Icon size={12} aria-hidden="true" /> {label}
        </span>
        <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{dep?.url ?? 'not configured (deployment setting)'}</span>
        {ok && <CheckCircle2 size={14} className="text-green-600 dark:text-green-400" aria-label="connected" />}
      </div>
    );
  };
  return (
    <section className={card} aria-labelledby="apps-title">
      <h2 id="apps-title" className="font-semibold mb-4 text-slate-900 dark:text-slate-100">Other apps</h2>
      {app('millm', 'miLLM', Server)}
      {app('mistudio', 'miStudio', Link2)}
      <div className="mt-4">
        <Field label="Hugging Face token" htmlFor="hf-token" hint="Stored encrypted and used only by workers. New repositories are private unless you choose otherwise.">
          <input id="hf-token" type="password" className={field} value={value} placeholder={token?.is_set ? token.value ?? '' : '(not set)'} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <Button disabled={!value} onClick={() => void saveSetting('hf_token', value).then((ok) => ok && setValue(''))}>
          Save the Hugging Face token
        </Button>
      </div>
    </section>
  );
}

export function SettingsPanel({ panel }: { panel: PanelDef }) {
  const loadSettings = useSettingsStore((s) => s.loadSettings);
  const roles = useSettingsStore((s) => s.roles);
  const error = useSettingsStore((s) => s.error);
  const notice = useSettingsStore((s) => s.notice);
  const loaded = useSettingsStore((s) => s.loaded);
  const health = useHealthStore((s) => s.health);
  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      {error && <p role="alert" className="mb-4 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {notice && !error && <p className="mb-4 text-sm text-green-700 dark:text-green-400">{notice}</p>}
      {!loaded && (
        <div className="mb-4 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" data-testid="settings-loading">
          <Spinner size="sm" /> Loading settings…
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <OperatorNameCard />
        <OtherAppsCard />
        {roles.map((role) => <RoleCard key={role.role} role={role} />)}
        <AgentAccessCard />
        <section className={card} aria-labelledby="shortcut-level-title">
          <h2 id="shortcut-level-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">Shortcut warning level</h2>
          <p className="text-xs mb-2 text-slate-500 dark:text-slate-400">
            The global margin a metadata column must clear, above chance and the permuted-label control, before the shortcut audit warns. Each
            dataset may override it, with a reason. Agents can read it but never change it (P-09).
          </p>
          <ShortcutLevelControl datasetId={null} />
        </section>
        <section className={card} aria-labelledby="storage-title">
          <h2 id="storage-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">Storage</h2>
          <p className="text-xs mb-2 text-slate-500 dark:text-slate-400">Rows live as Parquet on the data volume. PostgreSQL keeps metadata, labels and lineage only.</p>
          <div className="font-mono text-xs text-slate-700 dark:text-slate-300">{health?.dependencies.data_volume.path ?? '/data/dataworks'}</div>
          <StorageLimits />
        </section>
      </div>
    </>
  );
}

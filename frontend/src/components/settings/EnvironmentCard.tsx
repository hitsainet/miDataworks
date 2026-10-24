// Origin: miForge (Onegaishimas/miForge) frontend/src/components/panels/settings/SettingsPage.tsx @ 655c731
// Mode: adapt (docs/REUSE.md). Kept: two kinds of row — changeable while the app runs (typed editor,
// Save, "Use the environment value") and read-only from the environment; the editor follows the
// value's type in the payload, so this screen holds no copy of the backend's list. Changed
// (ENH-003 §2): each runtime row shows a plain-language label, the description, when a change takes
// effect, the environment value beside the value in force, and the last change; a reason can be
// given; "Needs a restart" and the approval rules are their own read-only groups; the last 50
// changes are listed. Feature 012.
import { useEffect, useState } from 'react';

import { Button } from '@/components/common/Button';
import { useSettingsStore } from '@/stores/settingsStore';
import type { EnvironmentRow, SettingChange } from '@/types/api';
import { bytesToGb, formatBytes, gbToBytes } from '@/utils/format';

const card = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';
const field =
  'w-full rounded-lg px-3 py-2 text-sm border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100';
const muted = 'text-xs text-slate-500 dark:text-slate-400';

/** A value as the operator reads it. */
/** A byte-count setting, entered in GB as the Storage limits were (T-02). */
export function isBytes(row: Pick<EnvironmentRow, 'key' | 'type'>): boolean {
  return row.type === 'int' && row.key.endsWith('_bytes');
}

export function showValue(row: Pick<EnvironmentRow, 'key' | 'type' | 'secret' | 'is_set'>, value: unknown): string {
  if (row.secret) return row.is_set ? 'set' : 'not set';
  if (value === null || value === undefined || value === '') return 'not set';
  if (row.type === 'bool') return value ? 'on' : 'off';
  if (isBytes(row) && typeof value === 'number') return `${formatBytes(value)} (${value.toLocaleString('en-US')} bytes)`;
  if (typeof value === 'number') return value.toLocaleString('en-US');
  return String(value);
}

function draftOf(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

/** The bounds the backend enforces, in words (the backend refuses anything outside them). */
function boundsText(row: EnvironmentRow): string {
  const parts: string[] = [];
  if (row.minimum !== null) parts.push(`at least ${row.minimum}`);
  if (row.exclusive_minimum !== null) parts.push(`above ${row.exclusive_minimum}`);
  if (row.maximum !== null) parts.push(`at most ${row.maximum}`);
  if (row.exclusive_maximum !== null) parts.push(`below ${row.exclusive_maximum}`);
  return parts.length ? `Allowed: ${parts.join(', ')}.` : '';
}

function when(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function RuntimeRow({ row }: { row: EnvironmentRow }) {
  const saveEnvironment = useSettingsStore((s) => s.saveEnvironment);
  const resetEnvironment = useSettingsStore((s) => s.resetEnvironment);
  const bytes = isBytes(row);
  const server = bytes ? bytesToGb(draftOf(row.value)) : draftOf(row.value);
  const [draft, setDraft] = useState(server);
  const [draftFrom, setDraftFrom] = useState(server);
  // A changed server value replaces the draft (adjusted during render, as RoleCard does).
  if (draftFrom !== server) {
    setDraftFrom(server);
    setDraft(server);
  }
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const id = (part: string) => `env-${part}-${row.key}`;
  const toSend = bytes ? gbToBytes(draft) : draft;

  const run = async (action: () => Promise<string | null>) => {
    setBusy(true);
    const refusal = await action();
    setBusy(false);
    setProblem(refusal);
    if (refusal === null) setReason('');
  };

  const control =
    row.type === 'bool' ? (
      <select id={id('input')} data-testid={id('input')} className={field} value={draft} onChange={(e) => setDraft(e.target.value)}>
        <option value="true">On</option>
        <option value="false">Off</option>
      </select>
    ) : row.type === 'choice' && row.choices ? (
      <select id={id('input')} data-testid={id('input')} className={field} value={draft} onChange={(e) => setDraft(e.target.value)}>
        {row.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    ) : (
      <input
        id={id('input')}
        data-testid={id('input')}
        className={`${field} font-mono`}
        type={row.type === 'int' || row.type === 'float' ? 'number' : 'text'}
        inputMode={bytes || row.type === 'float' ? 'decimal' : row.type === 'int' ? 'numeric' : row.type === 'address' ? 'url' : 'text'}
        step={bytes ? 1 : row.type === 'float' ? 'any' : undefined}
        min={bytes ? 0 : undefined}
        placeholder="not set"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
    );

  return (
    <li className="py-4 border-t border-slate-200 dark:border-slate-700 first:border-t-0" data-testid={`env-row-${row.key}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor={id('input')} className="font-medium text-sm text-slate-900 dark:text-slate-100">
          {bytes ? row.label.replace(/,? in bytes$/, '').replace(/ many bytes/, ' size') + ', in GB' : row.label}
        </label>
        <span className="flex flex-wrap gap-1.5">
          <span
            className={`text-[11px] px-2 py-0.5 rounded-full ${row.source === 'override' ? 'bg-slate-500/20 font-medium text-slate-800 dark:text-slate-200' : 'bg-slate-500/10 text-slate-600 dark:text-slate-400'}`}
            data-testid={id('source')}
          >
            {row.source === 'override' ? 'changed here' : 'environment value'}
          </span>
          {row.effect && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-500/10 text-slate-600 dark:text-slate-400" data-testid={id('effect')}>
              Takes effect: {row.effect}
            </span>
          )}
          {!row.agent_writable && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-800 dark:text-amber-300">operator only</span>
          )}
        </span>
      </div>
      <p className={`${muted} mt-1`}>
        <span className="font-mono">{row.env_var}</span> · {row.description}
        {row.effect_detail ? ` ${row.effect_detail}` : ''}
      </p>
      {row.caution && <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">{row.caution}</p>}
      <p className={`${muted} mt-1`} data-testid={id('values')}>
        In force: <span className="font-mono">{showValue(row, row.value)}</span> · Environment value:{' '}
        <span className="font-mono">{showValue(row, row.env_value)}</span>
        {row.updated_by ? ` · Last changed by ${row.updated_by}, ${when(row.updated_at)}` : ''}
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-start">
        <div>
          {control}
          {bytes ? (
            <div className={`${muted} mt-1`} data-testid={id('bytes')}>
              {toSend === null ? 'Enter a size above 0 GB.' : `Saves ${Number(toSend).toLocaleString('en-US')} bytes.`}
            </div>
          ) : (
            boundsText(row) && <div className={`${muted} mt-1`}>{boundsText(row)}</div>
          )}
        </div>
        <input
          aria-label={`Reason for changing ${row.label} (optional)`}
          data-testid={id('reason')}
          className={field}
          placeholder="Reason (optional)"
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={busy || toSend === null}
            data-testid={id('save')}
            onClick={() => toSend !== null && void run(() => saveEnvironment(row.key, toSend, reason.trim() || undefined))}
          >
            Save
          </Button>
          {row.source === 'override' && (
            <Button size="sm" variant="secondary" disabled={busy} data-testid={id('reset')} onClick={() => void run(() => resetEnvironment(row.key, reason.trim() || undefined))}>
              Use the environment value
            </Button>
          )}
        </div>
      </div>
      {problem && (
        <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-400" data-testid={id('error')}>
          {problem}
        </p>
      )}
    </li>
  );
}

function ReadOnlyList({ rows, testId, extra }: { rows: EnvironmentRow[]; testId: string; extra?: (row: EnvironmentRow) => string | null }) {
  return (
    <ul className="mt-2 divide-y divide-slate-200 dark:divide-slate-700" data-testid={testId}>
      {rows.map((row) => (
        <li key={row.key} className="py-2 text-sm" data-testid={`env-ro-${row.key}`}>
          <div className="flex flex-wrap justify-between gap-2">
            <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{row.env_var}</span>
            <span className="font-mono text-xs text-slate-900 dark:text-slate-100">{showValue(row, row.value)}</span>
          </div>
          <div className={muted}>{row.description}</div>
          {extra?.(row) && <div className="text-xs text-amber-800 dark:text-amber-300">{extra(row)}</div>}
        </li>
      ))}
    </ul>
  );
}

function History({ history }: { history: SettingChange[] }) {
  if (history.length === 0) return <p className={`${muted} mt-2`}>No changes yet.</p>;
  return (
    <ol className="mt-2 stack-y-1" data-testid="env-history">
      {history.map((change, index) => (
        <li key={`${change.key}-${change.changed_at}-${index}`} className="text-xs text-slate-700 dark:text-slate-300">
          <span className="font-mono">{change.key.toUpperCase()}</span>:{' '}
          {change.previous_value === null ? 'environment value' : <span className="font-mono">{change.previous_value || 'not set'}</span>} →{' '}
          {change.new_value === null ? 'environment value' : <span className="font-mono">{change.new_value || 'not set'}</span>} by {change.actor},{' '}
          {when(change.changed_at)}
          {change.reason ? ` — ${change.reason}` : ''}
        </li>
      ))}
    </ol>
  );
}

/** The Environment card on the Settings screen (feature 012). */
export function EnvironmentCard() {
  const environment = useSettingsStore((s) => s.environment);
  const error = useSettingsStore((s) => s.environmentError);
  const loadEnvironment = useSettingsStore((s) => s.loadEnvironment);
  useEffect(() => {
    void loadEnvironment();
  }, [loadEnvironment]);
  const rows = environment?.settings ?? [];
  const group = (name: EnvironmentRow['group']) => rows.filter((r) => r.group === name);

  return (
    <section className={`${card} lg:col-span-2`} aria-labelledby="environment-title" data-testid="environment-card">
      <h2 id="environment-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">
        Environment
      </h2>
      <p className={`${muted} mb-2`}>
        The deployment&apos;s environment sets every value here. The settings below can be changed while miDataworks runs, without a restart
        that would stop running jobs; &quot;Use the environment value&quot; removes a change. Each change is recorded with who made it and
        why. An agent&apos;s change waits for your approval.
      </p>
      {error && (
        <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <h3 className="mt-4 text-sm font-semibold text-slate-900 dark:text-slate-100">Changeable while miDataworks runs</h3>
      <ul data-testid="env-runtime">
        {group('runtime').map((row) => (
          <RuntimeRow key={row.key} row={row} />
        ))}
      </ul>
      <h3 className="mt-6 text-sm font-semibold text-slate-900 dark:text-slate-100">Needs a restart</h3>
      <p className={muted}>Changed in the deployment; restarting the backend stops every running job.</p>
      <ReadOnlyList rows={group('restart')} testId="env-restart" extra={(row) => row.restart} />
      <h3 className="mt-6 text-sm font-semibold text-slate-900 dark:text-slate-100">Approval rules</h3>
      <p className={muted}>
        When an agent&apos;s request waits for you. These are deployment configuration and cannot be changed here or by an agent.
      </p>
      <ReadOnlyList rows={group('approval_rule')} testId="env-approval-rules" />
      <details className="mt-6" data-testid="env-from-environment">
        <summary className="cursor-pointer text-sm font-semibold text-slate-900 dark:text-slate-100">From the environment (read-only)</summary>
        <p className={muted}>Secrets are shown only as set or not set, and passwords are removed from addresses.</p>
        <ReadOnlyList rows={group('environment')} testId="env-environment-list" />
      </details>
      <h3 className="mt-6 text-sm font-semibold text-slate-900 dark:text-slate-100">Recent changes</h3>
      <History history={environment?.history ?? []} />
    </section>
  );
}

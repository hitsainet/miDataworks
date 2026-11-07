// The Rule signals screen (feature 016, FR-016.40). Read-only: rule sets are written through MCP and
// REST (D22). Tabs: Rule sets (behaviour filter, archived hidden by default; the detail with its
// signals, outputs, vetoes, cases, last validation and the builds that used it), Validators (each
// reason with its cases) and Preview (sends nothing until Preview is clicked). Spans are counted in
// characters (code points); a proximity signal shows its declared unit. Times are US Eastern.
// A rule set's or a validator's detail opens directly under its row; its link again, the X or
// Escape closes it (operator, 2026-10-31).
import { Fragment, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { CloseButton, useEscapeToClose } from '@/components/common/DetailPopup';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useRuleSignalsStore } from '@/stores/ruleSignalsStore';
import type { DerivationFigures, RuleDerivation, RuleSetDetail, RuleSetSignal } from '@/types/ruleSignals';
import { formatEt, shortId } from '@/utils/format';

type Tab = 'rule-sets' | 'validators' | 'derivations' | 'preview';
const TABS: Array<[Tab, string]> = [
  ['rule-sets', 'Rule sets'],
  ['validators', 'Validators'],
  ['derivations', 'Derivations'],
  ['preview', 'Preview'],
];

const box = 'rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-4 mb-4';
const th = 'text-left font-medium text-slate-500 dark:text-slate-400 px-2 py-1';
const td = 'px-2 py-1 align-top';

function signalSummary(signal: RuleSetSignal): string {
  if (signal.kind === 'proximity') {
    return `proximity within ${String(signal.max_distance)} ${signal.unit === 'utf8_bytes' ? 'bytes (UTF-8)' : 'characters'}`;
  }
  return signal.kind.replace('_', ' ');
}

const link = 'text-indigo-600 dark:text-indigo-400 hover:underline focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-control';

/** The row under a list row that holds its open detail, spanning every column. */
function DetailRow({ columns, id, children }: { columns: number; id: string; children: ReactNode }) {
  return (
    <tr id={id}>
      <td colSpan={columns} className="px-0 pt-1">{children}</td>
    </tr>
  );
}

function RuleSetDetailView({ detail, onClose }: { detail: RuleSetDetail; onClose: () => void }) {
  const doc = detail.document;
  return (
    <div className={box} data-testid="rule-set-detail">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-lg font-semibold">{detail.name}</h2>
        <Badge variant="neutral">version {detail.version}</Badge>
        <Badge variant="primary">{detail.behaviour_key}</Badge>
        {detail.archived && <Badge variant="warning">Archived</Badge>}
        <CloseButton onClose={onClose} what="this rule set" className="ml-auto" />
      </div>
      <p className="text-sm text-slate-600 dark:text-slate-300 mb-2">{doc.description}</p>
      <p className="text-xs text-slate-500 mb-3">
        sha256 <span className="font-mono">{shortId(detail.sha256, 16)}</span>
        {detail.last_validation && (
          <> · every case passed at save, {formatEt(detail.last_validation.at) ?? 'time not recorded'} ({detail.last_validation.cases} cases)</>
        )}
      </p>
      <h3 className="font-medium mt-3 mb-1">Signals</h3>
      <ul className="text-sm list-disc ml-5">
        {doc.signals.map((s) => (
          <li key={s.id}><span className="font-mono">{s.id}</span>: {signalSummary(s)}</li>
        ))}
      </ul>
      <h3 className="font-medium mt-3 mb-1">Outputs</h3>
      <ul className="text-sm list-disc ml-5">
        {doc.outputs.map((o) => (
          <li key={o.column}><span className="font-mono">{o.column}</span> ({o.type})</li>
        ))}
      </ul>
      {(doc.vetoes ?? []).length > 0 && (
        <>
          <h3 className="font-medium mt-3 mb-1">Vetoes</h3>
          <ul className="text-sm list-disc ml-5">
            {(doc.vetoes ?? []).map((v) => (
              <li key={v.id}><span className="font-mono">{v.id}</span>: when {v.when}, removes {v.removes.join(', ')}</li>
            ))}
          </ul>
        </>
      )}
      <h3 className="font-medium mt-3 mb-1">Cases ({doc.cases.length})</h3>
      <ul className="text-sm list-disc ml-5">
        {doc.cases.map((c) => (
          <li key={c.id}><span className="font-mono">{c.id}</span>{c.note ? `: ${c.note}` : ''}</li>
        ))}
      </ul>
      <h3 className="font-medium mt-3 mb-1">Builds that used it</h3>
      {detail.used_by.length === 0 ? (
        <p className="text-sm text-slate-500">No build has run this version yet.</p>
      ) : (
        <ul className="text-sm list-disc ml-5">
          {detail.used_by.map((u) => (
            <li key={`${u.version_id}-${u.step_index}`}>version {u.number} ({shortId(u.version_id)}), step {u.step_index}, used to {u.use.replace('_', ' ')}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RuleSetsTab() {
  const ruleSets = useRuleSignalsStore((s) => s.ruleSets);
  const current = useRuleSignalsStore((s) => s.current);
  const fetchRuleSets = useRuleSignalsStore((s) => s.fetchRuleSets);
  const openRuleSet = useRuleSignalsStore((s) => s.openRuleSet);
  const closeRuleSet = useRuleSignalsStore((s) => s.closeRuleSet);
  useEscapeToClose(Boolean(current), closeRuleSet);
  const [behaviourKey, setBehaviourKey] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  useEffect(() => {
    void fetchRuleSets(behaviourKey, showArchived);
  }, [fetchRuleSets, behaviourKey, showArchived]);
  return (
    <>
      <div className="flex flex-wrap items-end gap-4 mb-4">
        <label className="text-sm">
          Behaviour key
          <input
            className="ml-2 rounded-control border border-slate-300 dark:border-slate-600 bg-transparent px-2 py-1"
            value={behaviourKey}
            onChange={(e) => setBehaviourKey(e.target.value)}
            placeholder="all behaviours"
          />
        </label>
        <label className="text-sm flex items-center gap-2">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          Show archived rule sets
        </label>
      </div>
      {ruleSets.length === 0 ? (
        <p className="text-slate-500" data-testid="rule-sets-empty">No rule sets yet. An agent saves them with dataworks_save_rule_set.</p>
      ) : (
        <table className="w-full text-sm mb-4" data-testid="rule-sets-table">
          <thead>
            <tr><th className={th}>Name</th><th className={th}>Behaviour</th><th className={th}>Version</th><th className={th}>Cases</th><th className={th}>Last check</th></tr>
          </thead>
          <tbody>
            {ruleSets.map((r) => {
              const isOpen = current?.name === r.name;
              return (
                <Fragment key={r.name}>
                  <tr className="border-t border-slate-200 dark:border-slate-700">
                    <td className={td}>
                      <button
                        type="button"
                        className={link}
                        aria-expanded={isOpen}
                        aria-controls={isOpen ? 'rule-set-detail-row' : undefined}
                        onClick={() => (isOpen ? closeRuleSet() : void openRuleSet(r.name))}
                      >
                        {r.name}
                      </button>
                      {r.archived && <Badge variant="warning" size="sm" className="ml-2">Archived</Badge>}
                    </td>
                    <td className={td}>{r.behaviour_key}</td>
                    <td className={td}>{r.version ?? '-'}</td>
                    <td className={td}>{r.counts.cases ?? 0}</td>
                    <td className={td}>{formatEt(r.last_validation?.at) ?? 'not recorded'}</td>
                  </tr>
                  {isOpen && current && (
                    <DetailRow columns={5} id="rule-set-detail-row">
                      <RuleSetDetailView detail={current} onClose={closeRuleSet} />
                    </DetailRow>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}

function ValidatorsTab() {
  const validators = useRuleSignalsStore((s) => s.validators);
  const validator = useRuleSignalsStore((s) => s.validator);
  const fetchValidators = useRuleSignalsStore((s) => s.fetchValidators);
  const openValidator = useRuleSignalsStore((s) => s.openValidator);
  const closeValidator = useRuleSignalsStore((s) => s.closeValidator);
  useEscapeToClose(Boolean(validator), closeValidator);
  useEffect(() => {
    void fetchValidators();
  }, [fetchValidators]);
  return (
    <>
      <table className="w-full text-sm mb-4" data-testid="validators-table">
        <thead>
          <tr><th className={th}>Validator</th><th className={th}>Reasons</th><th className={th}>Cases</th><th className={th}>Newer version</th></tr>
        </thead>
        <tbody>
          {validators.map((v) => {
            const isOpen = validator?.ref === v.ref;
            return (
              <Fragment key={v.ref}>
                <tr className="border-t border-slate-200 dark:border-slate-700">
                  <td className={td}>
                    <button
                      type="button"
                      className={`font-mono ${link}`}
                      aria-expanded={isOpen}
                      aria-controls={isOpen ? 'validator-detail-row' : undefined}
                      onClick={() => (isOpen ? closeValidator() : void openValidator(v.id, v.version))}
                    >
                      {v.ref}
                    </button>
                  </td>
                  <td className={td}>{v.reasons.join(', ')}</td>
                  <td className={td}>{v.case_count}</td>
                  <td className={td}>{v.superseded_by ? `version ${v.superseded_by}` : 'none'}</td>
                </tr>
                {isOpen && validator && (
                  <DetailRow columns={4} id="validator-detail-row">
                    <div className={box} data-testid="validator-detail">
                      <div className="flex items-center gap-2 mb-1">
                        <h2 className="text-lg font-semibold font-mono">{validator.ref}</h2>
                        <CloseButton onClose={closeValidator} what="this validator" className="ml-auto" />
                      </div>
                      <p className="text-sm mb-2">{validator.description}</p>
                      {validator.reasons.map((reason) => {
                        const cases = validator.cases.filter((c) => (c.expect as { reason?: string }).reason === reason);
                        return (
                          <div key={reason} className="mb-2">
                            <span className="font-mono text-sm">{reason}</span>
                            <span className="text-xs text-slate-500 ml-2">{cases.length} {cases.length === 1 ? 'case' : 'cases'}</span>
                            <ul className="text-xs list-disc ml-5">
                              {cases.map((c) => <li key={c.id}><span className="font-mono">{c.id}</span>{c.note ? `: ${c.note}` : ''}</li>)}
                            </ul>
                          </div>
                        );
                      })}
                    </div>
                  </DetailRow>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

const percent = (value: number | null) => (value == null ? '-' : `${(value * 100).toFixed(1)}%`);

/** "Not measured" is a word in neutral slate: never a number, a check mark or green. */
function FiguresCells({ figures, state }: { figures: DerivationFigures; state: RuleDerivation['state'] }) {
  if (state === 'not_measured') {
    return <td className={`${td} text-slate-500`} colSpan={3}>Not measured</td>;
  }
  return (
    <>
      <td className={td}>{percent(figures.precision)}</td>
      <td className={td}>{percent(figures.recall)}</td>
      <td className={td}>{percent(figures.coverage)}</td>
    </>
  );
}

function DerivationsTab() {
  const derivation = useRuleSignalsStore((s) => s.derivation);
  const openDerivation = useRuleSignalsStore((s) => s.openDerivation);
  const [id, setId] = useState('');
  const result = derivation?.result;
  return (
    <>
      <p className="text-sm mb-2">An agent starts a derivation with dataworks_derive_rule_set; paste its id to read the figures. Figures are measured on held-out rows only.</p>
      <div className="flex gap-2 mb-4">
        <input aria-label="Derivation id" className="rounded-control border border-slate-300 dark:border-slate-600 bg-transparent px-2 py-1 font-mono text-sm" value={id} onChange={(e) => setId(e.target.value)} placeholder="rdv_..." />
        <Button onClick={() => void openDerivation(id)}>Open</Button>
      </div>
      {derivation && (
        <div className={box} data-testid="derivation-detail">
          <div className="flex items-center gap-2 mb-2">
            <span className="font-mono">{derivation.id}</span>
            {derivation.state === 'not_measured' && <Badge variant="neutral">Not measured</Badge>}
            {derivation.state === 'low_coverage' && <Badge variant="warning">Low coverage</Badge>}
            {derivation.state === 'measured' && <Badge variant="primary">Measured</Badge>}
            {derivation.state == null && <Badge variant="neutral">{derivation.status}</Badge>}
          </div>
          {result && (
            <>
              <p className="text-xs text-slate-500 mb-2">
                Held-out rows: {result.held_out_per_class.positive} positive, {result.held_out_per_class.negative} negative · seed {result.seed}
                {derivation.completed_at ? ` · ${formatEt(derivation.completed_at) ?? ''}` : ''}
              </p>
              <table className="w-full text-sm">
                <thead><tr><th className={th}>Literal</th><th className={th}>Precision</th><th className={th}>Recall</th><th className={th}>Coverage</th></tr></thead>
                <tbody>
                  <tr className="font-medium"><td className={td}>Whole draft</td><FiguresCells figures={result.draft} state={derivation.state} /></tr>
                  {result.literals.map((lit, n) => (
                    <tr key={lit.literal}>
                      <td className={`${td} font-mono`}>{lit.literal}</td>
                      <FiguresCells figures={result.signals[`d${n + 1}`]} state={derivation.state} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </>
  );
}

function PreviewTab() {
  const current = useRuleSignalsStore((s) => s.current);
  const preview = useRuleSignalsStore((s) => s.preview);
  const runPreview = useRuleSignalsStore((s) => s.runPreview);
  const [text, setText] = useState('');
  if (!current) {
    return <p className="text-slate-500">Open a rule set on the Rule sets tab first, then try it on a text here.</p>;
  }
  return (
    <>
      <p className="text-sm mb-2">
        Try <span className="font-mono">{current.name}</span> version {current.version} on a text. The text is not saved or logged, and nothing is sent until you click Preview.
      </p>
      <textarea
        aria-label="Text to preview"
        className="w-full h-28 rounded-control border border-slate-300 dark:border-slate-600 bg-transparent p-2 text-sm mb-2"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Button onClick={() => void runPreview(text)}>Preview</Button>
      {preview && (
        <div className={`${box} mt-4`} data-testid="preview-result">
          <p className="text-sm mb-2">Vetoed: {preview.vetoed ? 'yes' : 'no'}</p>
          <table className="w-full text-sm mb-2">
            <thead><tr><th className={th}>Output</th><th className={th}>Value</th></tr></thead>
            <tbody>
              {Object.entries(preview.outputs).map(([column, value]) => (
                <tr key={column}><td className={`${td} font-mono`}>{column}</td><td className={td}>{JSON.stringify(value)}</td></tr>
              ))}
            </tbody>
          </table>
          <table className="w-full text-sm">
            <thead><tr><th className={th}>Signal</th><th className={th}>Fired</th><th className={th}>Spans, in characters (code points)</th></tr></thead>
            <tbody>
              {Object.entries(preview.signals).map(([id, verdict]) => (
                <tr key={id}>
                  <td className={`${td} font-mono`}>{id}</td>
                  <td className={td}>{verdict.fired ? 'yes' : 'no'}</td>
                  <td className={td}>{verdict.spans.map(([a, b]) => `${a}-${b}`).join(', ') || 'none'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function RuleSignalsPanel({ panel }: { panel: PanelDef }) {
  const [tab, setTab] = useState<Tab>('rule-sets');
  const error = useRuleSignalsStore((s) => s.error);
  return (
    <>
      <PageHead help="rule-signals" title={panel.title} subtitle={panel.subtitle} />
      <div role="tablist" className="flex gap-2 mb-4">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={tab === id}
            className={`px-3 py-1 rounded-control text-sm ${tab === id ? 'bg-indigo-600 text-white' : 'bg-slate-100 dark:bg-slate-800'}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <div role="alert" className="mb-4 rounded-card border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      {tab === 'rule-sets' && <RuleSetsTab />}
      {tab === 'validators' && <ValidatorsTab />}
      {tab === 'derivations' && <DerivationsTab />}
      {tab === 'preview' && <PreviewTab />}
    </>
  );
}

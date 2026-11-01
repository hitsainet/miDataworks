// Detector sets (009 FPRD section 4; mockup `DetectorSets()`): the list of sets, a new-set form, and
// one set's roles, length profiles, checks, send, run-request skeleton and results. The open set is in
// the URL (#/detector-sets/<id>), so a refresh keeps it and the browser's Back returns to the list
// (operator, 2026-10-31).
import { ArrowLeft } from 'lucide-react';
import { BehaviourFilter } from '@/components/behaviour/Behaviour017';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/common/Button';
import { CloseButton, useEscapeToClose } from '@/components/common/DetailPopup';
import { DetectorChecks } from '@/components/detectorSets/DetectorChecks';
import { DetectorSetCard } from '@/components/detectorSets/DetectorSetCard';
import { LengthProfilePanel } from '@/components/detectorSets/LengthProfilePanel';
import { MinimalPairsSection } from '@/components/detectorSets/MinimalPairs';
import { ResultsPanel } from '@/components/detectorSets/ResultsPanel';
import { EMPTY_ROLE, RoleEditor } from '@/components/detectorSets/RoleEditor';
import { RoleTable } from '@/components/detectorSets/RoleTable';
import { MISTUDIO_UNCONFIGURED } from '@/components/detectorSets/format';
import { RunRequestSkeleton } from '@/components/detectorSets/RunRequestSkeleton';
import { SendDialog } from '@/components/detectorSets/SendDialog';
import { SendHistory } from '@/components/detectorSets/SendHistory';
import { SendProgress } from '@/components/detectorSets/SendProgress';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useDetectorSend } from '@/hooks/useDetectorSend';
import { useHashSub } from '@/hooks/useHashSub';
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useHealthStore } from '@/stores/healthStore';
import type { RoleIn } from '@/types/detectorSets';
import { behaviourKeyOk } from '@/utils/behaviourKey';
import { hashPath, navigate } from '@/utils/navigate';

const input =
  'rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-hidden';
const card = 'mb-5 rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/60 p-5';
const primary = 'rounded-control bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500';
const secondary = 'rounded-control border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50';


function BehaviourKeyInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const bad = !behaviourKeyOk(value);
  return (
    <label className="flex flex-col text-xs">
      <input
        aria-label="Behaviour key"
        placeholder="behaviour key, e.g. pii (optional)"
        className={input}
        value={value}
        aria-invalid={bad}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className={bad ? 'text-red-600 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}>
        Lower case letters, digits and hyphens, 2 to 64 characters; the key miStudio's behaviour catalogue uses.
      </span>
    </label>
  );
}

function BehaviourKeyEditor({ current }: { current: string | null | undefined }) {
  const updateBehaviourKey = useDetectorSetsStore((s) => s.updateBehaviourKey);
  const [value, setValue] = useState(current ?? '');
  return (
    <div className="mt-1 flex flex-wrap items-end gap-2" data-testid="behaviour-key-editor">
      <BehaviourKeyInput value={value} onChange={setValue} />
      <button
        type="button"
        className={secondary}
        disabled={!behaviourKeyOk(value) || value === (current ?? '')}
        onClick={() => void updateBehaviourKey(value)}
      >
        Save behaviour key
      </button>
    </div>
  );
}

function NewSetForm({ onDone }: { onDone: () => void }) {
  const createSet = useDetectorSetsStore((s) => s.createSet);
  const [name, setName] = useState('');
  const [meaning, setMeaning] = useState('');
  const [behaviour, setBehaviour] = useState('');
  const [roles, setRoles] = useState<RoleIn[]>([
    EMPTY_ROLE('train'),
    EMPTY_ROLE('id_test'),
    EMPTY_ROLE('ood_eval'),
    EMPTY_ROLE('calibration_negatives'),
  ]);
  return (
    <section className={card} data-testid="new-set-form">
      <h2 className="mb-3 text-base font-semibold">New detector set</h2>
      <div className="mb-3 flex flex-wrap gap-2">
        <input aria-label="Detector set name" placeholder="name (lower case, digits, hyphens)" className={input} value={name} onChange={(e) => setName(e.target.value)} />
        <input aria-label="Positive class meaning" placeholder="What the positive class means" className={`${input} min-w-64 flex-1`} value={meaning} onChange={(e) => setMeaning(e.target.value)} />
        <BehaviourKeyInput value={behaviour} onChange={setBehaviour} />
      </div>
      <div className="stack-y-3">
        {roles.map((r, i) => (
          <RoleEditor
            key={`${r.role}-${i}`}
            value={r}
            onChange={(next) => setRoles(roles.map((x, j) => (j === i ? next : x)))}
            onRemove={r.role === 'ood_eval' && roles.filter((x) => x.role === 'ood_eval').length > 1 ? () => setRoles(roles.filter((_, j) => j !== i)) : undefined}
          />
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <button type="button" className={secondary} onClick={() => setRoles([...roles, EMPTY_ROLE('ood_eval')])}>Add an out-of-distribution role</button>
        <button
          type="button"
          className={primary}
          disabled={!name || !behaviourKeyOk(behaviour)}
          onClick={async () => {
            const created = await createSet({
              name,
              positive_meaning: meaning,
              ...(behaviour ? { behaviour_key: behaviour } : {}),
              roles,
            });
            if (created) onDone();
          }}
        >
          Create detector set
        </button>
        <button type="button" className={secondary} onClick={onDone}>Close</button>
      </div>
    </section>
  );
}

function SetDetail({ onClose }: { onClose: () => void }) {
  const current = useDetectorSetsStore((s) => s.current)!;
  const checks = useDetectorSetsStore((s) => s.checks);
  const send = useDetectorSetsStore((s) => s.send);
  const results = useDetectorSetsStore((s) => s.results);
  const notice = useDetectorSetsStore((s) => s.notice);
  const runChecks = useDetectorSetsStore((s) => s.runChecks);
  const startSend = useDetectorSetsStore((s) => s.startSend);
  const fetchSend = useDetectorSetsStore((s) => s.fetchSend);
  const resumeSend = useDetectorSetsStore((s) => s.resumeSend);
  const cancelSend = useDetectorSetsStore((s) => s.cancelSend);
  const refreshResults = useDetectorSetsStore((s) => s.refreshResults);
  // Runtime, from GET /api/health: unknown (null) never blocks; the API refuses on its own.
  const noMiStudio = useHealthStore((s) => s.health?.dependencies.mistudio?.configured === false);
  const archive = useDetectorSetsStore((s) => s.archive);
  const fetchResults = useDetectorSetsStore((s) => s.fetchResults);
  const updateRoles = useDetectorSetsStore((s) => s.updateRoles);
  const [editing, setEditing] = useState<RoleIn[] | null>(null);
  useDetectorSend(send?.id ?? null, send?.state ?? null);
  // The set's detail stands in for the list (the card is hidden), so the X and Escape close it
  // (operator, 2026-10-31), as Back to all sets does.
  useEscapeToClose(true, onClose);
  useEffect(() => {
    void fetchResults();
  }, [current.id, fetchResults]);

  const cal = current.roles.find((r) => r.role === 'calibration_negatives');
  const calProfile = cal ? checks?.profiles[cal.id] ?? current.profiles[cal.id] ?? null : null;
  const d5 = checks?.outcomes.find((o) => o.code === 'D-5') ?? null;
  return (
    <>
      {/* The way back sits first, top left, as on an open operator (operator, 2026-10-31: there was
          "no way to return" — the button sat last in the action row and read as one more action). */}
      <div className="mb-3 flex items-center gap-2" data-testid="set-detail-nav">
        <Button variant="ghost" size="sm" leftIcon={<ArrowLeft size={14} />} onClick={onClose}>
          Back to all sets
        </Button>
        <CloseButton onClose={onClose} what="this detector set" className="ml-auto" />
      </div>
      <section className={card} data-testid="set-detail">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">{current.name}</h2>
            {current.positive_meaning && <p className="text-xs text-slate-500 dark:text-slate-400">Positive means: {current.positive_meaning}</p>}
            <BehaviourKeyEditor key={current.id} current={current.behaviour_key} />
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondary} onClick={() => void runChecks()}>Run checks</button>
            <button
              type="button"
              className={secondary}
              onClick={() =>
                setEditing(
                  current.roles.map((r) => ({
                    role: r.role,
                    version_id: r.version_id,
                    split: r.split,
                    input_column: r.input_column,
                    label_column: r.label_column,
                    label_mapping: r.label_mapping,
                    pair_column: r.pair_column,
                    label_source_columns: r.label_source_columns ?? [],
                    negatives_basis: r.negatives_basis,
                    display_name: r.display_name || null,
                  })),
                )
              }
            >
              Edit roles
            </button>
            {!current.archived && <button type="button" className={secondary} onClick={() => void archive()}>Archive</button>}
          </div>
        </div>
        <RoleTable roles={current.roles} checks={checks} />
        {editing && (
          <div className="mt-3 stack-y-3" data-testid="role-editing">
            {editing.map((r, i) => (
              <RoleEditor
                key={`${r.role}-${i}`}
                value={r}
                values={checks?.label_values[current.roles[i]?.id ?? '']}
                onChange={(next) => setEditing(editing.map((x, j) => (j === i ? next : x)))}
              />
            ))}
            <div className="flex gap-2">
              <button
                type="button"
                className={primary}
                onClick={async () => {
                  if (await updateRoles(editing)) setEditing(null);
                }}
              >
                Save roles
              </button>
              <button type="button" className={secondary} onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>
      <section className={card}>
        <h3 className="mb-2 text-sm font-semibold">Calibration negatives against the monitored text</h3>
        <LengthProfilePanel calibration={calProfile} monitored={checks?.monitored?.profile ?? null} check={d5} />
      </section>
      {checks && (
        <section className={card}>
          <h3 className="mb-2 text-sm font-semibold">Checks before sending</h3>
          <DetectorChecks outcomes={checks.outcomes} />
        </section>
      )}
      <section className={card}>
        <h3 className="mb-2 text-sm font-semibold">Send to miStudio</h3>
        {noMiStudio && <p className="mb-2 rounded-control border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100" data-testid="mistudio-unconfigured">{MISTUDIO_UNCONFIGURED}</p>}
        <SendDialog roles={current.roles} refusal={checks?.first_refusal ?? null} checked={checks !== null} unavailable={noMiStudio ? 'Sending needs miStudio, which is not configured.' : null} onSend={(body) => void startSend(body)} />
        {notice && <p className="mt-2 text-sm" data-testid="send-notice">{notice}</p>}
        {send && <div className="mt-3"><SendProgress send={send} onResume={() => void resumeSend()} onCancel={() => void cancelSend()} /></div>}
        {send?.run_request && (
          <div className="mt-3">
            <h4 className="mb-1 text-xs font-semibold">Run request for miStudio</h4>
            <RunRequestSkeleton request={send.run_request} mistudioUrl={send.mistudio_base_url} />
          </div>
        )}
        <div className="mt-3"><SendHistory sends={current.sends ?? []} onOpen={(id) => void fetchSend(id)} /></div>
      </section>
      <section className={card}>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Results back from miStudio</h3>
          <button type="button" className={secondary} disabled={noMiStudio} title={noMiStudio ? 'Refreshing needs miStudio, which is not configured.' : undefined} onClick={() => void refreshResults()}>Refresh results</button>
        </div>
        <ResultsPanel results={results} standalone={noMiStudio} />
      </section>
    </>
  );
}

export function DetectorSetsPanel({ panel }: { panel: PanelDef }) {
  const sets = useDetectorSetsStore((s) => s.sets);
  const current = useDetectorSetsStore((s) => s.current);
  const error = useDetectorSetsStore((s) => s.error);
  const fetchSets = useDetectorSetsStore((s) => s.fetchSets);
  const behaviourKey = useDetectorSetsStore((s) => s.behaviourKey);
  const setBehaviourKey = useDetectorSetsStore((s) => s.setBehaviourKey);
  const close = useDetectorSetsStore((s) => s.close);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    void fetchSets();
  }, [fetchSets]);

  // The URL names the open set: opening one adds #/detector-sets/<id> to the browser's history, and
  // Back to all sets, the X, Escape or the browser's Back return to #/detector-sets. A refresh opens
  // the set the URL names.
  const openId = useHashSub('detector-sets');
  useEffect(() => {
    const state = useDetectorSetsStore.getState();
    if (openId) {
      if (state.current?.id !== openId) void state.open(openId);
    } else if (state.current) {
      state.close();
    }
  }, [openId]);
  // A set opened another way (just created) is written to the URL too, as a new history entry. The
  // set left open from an earlier visit is not: the URL decides on arrival.
  const currentId = current?.id ?? null;
  const shownId = useRef(currentId);
  useEffect(() => {
    if (currentId === shownId.current) return;
    shownId.current = currentId;
    const at = hashPath();
    if (currentId && at.panel === 'detector-sets' && at.sub !== currentId) {
      window.history.pushState(null, '', `#/detector-sets/${encodeURIComponent(currentId)}`);
    }
  }, [currentId]);
  const backToList = () => {
    close();
    if (hashPath().sub) navigate('detector-sets');
  };

  return (
    <>
      <PageHead help="detector-sets" title={panel.title} subtitle={panel.subtitle} />
      {error && <div role="alert" className="mb-4 rounded-card border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      {current ? (
        <SetDetail onClose={backToList} />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-4">
            <button type="button" className={primary} onClick={() => setCreating(true)}>New detector set</button>
            <BehaviourFilter value={behaviourKey} onChange={(k) => void setBehaviourKey(k)} testId="sets-behaviour-filter" />
          </div>
          {creating && <NewSetForm onDone={() => setCreating(false)} />}
          {sets.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-sets">No detector sets yet. Create one from versions holding training rows, a test, out-of-distribution rows and calibration negatives.</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {sets.map((s) => <DetectorSetCard key={s.id} set={s} onOpen={() => navigate('detector-sets', s.id)} />)}
            </div>
          )}
          <MinimalPairsSection />
        </>
      )}
    </>
  );
}

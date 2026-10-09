// Detector sets (009 FPRD section 4; mockup `DetectorSets()`): the list of sets, a new-set form, and
// one set's roles, length profiles, checks, send, run-request skeleton and results.
import { useEffect, useState } from 'react';

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
import { useDetectorSetsStore } from '@/stores/detectorSetsStore';
import { useHealthStore } from '@/stores/healthStore';
import type { RoleIn } from '@/types/detectorSets';

const input =
  'rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';
const card = 'mb-5 rounded-xl border border-slate-200 dark:border-indigo-400/10 bg-white dark:bg-slate-900/60 p-5';
const primary = 'rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500';
const secondary = 'rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50';

function NewSetForm({ onDone }: { onDone: () => void }) {
  const createSet = useDetectorSetsStore((s) => s.createSet);
  const [name, setName] = useState('');
  const [meaning, setMeaning] = useState('');
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
      </div>
      <div className="space-y-3">
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
          disabled={!name}
          onClick={async () => {
            const created = await createSet({ name, positive_meaning: meaning, roles });
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

function SetDetail() {
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
  const close = useDetectorSetsStore((s) => s.close);
  const fetchResults = useDetectorSetsStore((s) => s.fetchResults);
  const updateRoles = useDetectorSetsStore((s) => s.updateRoles);
  const [editing, setEditing] = useState<RoleIn[] | null>(null);
  useDetectorSend(send?.id ?? null, send?.state ?? null);
  useEffect(() => {
    void fetchResults();
  }, [current.id, fetchResults]);

  const cal = current.roles.find((r) => r.role === 'calibration_negatives');
  const calProfile = cal ? checks?.profiles[cal.id] ?? current.profiles[cal.id] ?? null : null;
  const d5 = checks?.outcomes.find((o) => o.code === 'D-5') ?? null;
  return (
    <>
      <section className={card} data-testid="set-detail">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">{current.name}</h2>
            {current.positive_meaning && <p className="text-xs text-slate-500 dark:text-slate-400">Positive means: {current.positive_meaning}</p>}
          </div>
          <div className="flex gap-2">
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
            <button type="button" className={secondary} onClick={close}>Back to all sets</button>
          </div>
        </div>
        <RoleTable roles={current.roles} checks={checks} />
        {editing && (
          <div className="mt-3 space-y-3" data-testid="role-editing">
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
        {noMiStudio && <p className="mb-2 rounded-md border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100" data-testid="mistudio-unconfigured">{MISTUDIO_UNCONFIGURED}</p>}
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
  const open = useDetectorSetsStore((s) => s.open);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    void fetchSets();
  }, [fetchSets]);

  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      {current ? (
        <SetDetail />
      ) : (
        <>
          <div className="mb-4">
            <button type="button" className={primary} onClick={() => setCreating(true)}>New detector set</button>
          </div>
          {creating && <NewSetForm onDone={() => setCreating(false)} />}
          {sets.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-sets">No detector sets yet. Create one from versions holding training rows, a test, out-of-distribution rows and calibration negatives.</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {sets.map((s) => <DetectorSetCard key={s.id} set={s} onOpen={() => void open(s.id)} />)}
            </div>
          )}
          <MinimalPairsSection />
        </>
      )}
    </>
  );
}

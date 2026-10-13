// Calibration (FR-006.33; mockup `Judges()`): records grouped by question, newest per labeler first;
// "Add a calibration set"; "Compute a record" starts a job and follows its room.
import { useEffect, useMemo, useState } from 'react';

import { AddCalibrationSetDialog } from '@/components/calibration/AddCalibrationSetDialog';
import { RecordCard } from '@/components/calibration/RecordCard';
import { TargetEditor } from '@/components/calibration/TargetEditor';
import { PageHead } from '@/components/layout/PageHead';
import type { PanelDef } from '@/config/panels';
import { useCalibrationJob } from '@/hooks/useCalibrationJob';
import { useCalibrationStore } from '@/stores/calibrationStore';
import { useReviewStore } from '@/stores/reviewStore';
import type { CalibrationRecord } from '@/types/calibration';

const input = 'rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none';

export function latestPerLabeler(records: CalibrationRecord[]): Array<[string, string, CalibrationRecord[]]> {
  const byQuestion = new Map<string, { question: string; byLabeler: Map<string, CalibrationRecord> }>();
  for (const r of [...records].sort((a, b) => b.created_at.localeCompare(a.created_at))) {
    const group = byQuestion.get(r.question_hash) ?? { question: r.question, byLabeler: new Map() };
    if (!group.byLabeler.has(r.labeler_identity_hash)) group.byLabeler.set(r.labeler_identity_hash, r);
    byQuestion.set(r.question_hash, group);
  }
  return [...byQuestion.entries()].map(([hash, g]) => [hash, g.question, [...g.byLabeler.values()]]);
}

export function CalibrationPanel({ panel }: { panel: PanelDef }) {
  const records = useCalibrationStore((s) => s.records);
  const sets = useCalibrationStore((s) => s.sets);
  const jobs = useCalibrationStore((s) => s.jobs);
  const error = useCalibrationStore((s) => s.error);
  const fetchRecords = useCalibrationStore((s) => s.fetchRecords);
  const fetchSets = useCalibrationStore((s) => s.fetchSets);
  const startCompute = useCalibrationStore((s) => s.startCompute);
  const fetchQueues = useReviewStore((s) => s.fetchQueues);
  const [adding, setAdding] = useState(false);
  const [setId, setSetId] = useState('');
  const [runId, setRunId] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  useCalibrationJob(jobId);

  useEffect(() => {
    void fetchRecords();
    void fetchSets();
    void fetchQueues();
  }, [fetchRecords, fetchSets, fetchQueues]);

  const groups = useMemo(() => latestPerLabeler(records), [records]);
  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      <details className="mb-4 text-sm text-slate-600 dark:text-slate-300" data-testid="calibration-help">
        <summary className="cursor-pointer">What these numbers mean</summary>
        <p className="mt-2">
          AUROC (area under the receiver operating characteristic curve) is the chance the labeler scores a row people
          called positive above one they called negative; 0.5 is chance. The bracket is its 95% confidence interval.
          The held-out human rater is one person's rating scored the same way against the others: a labeler level with
          it is as good as a person. A labeler passes when its interval's lower bound reaches the operator's target, or
          when it is level with the held-out rater, or — with neither — when the lower bound reaches 0.70.
        </p>
      </details>
      {error && <div role="alert" className="mb-4 rounded-lg border border-red-300 dark:border-red-500/40 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-sm">{error}</div>}
      <div className="mb-5 flex flex-wrap items-end gap-2">
        <button type="button" className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm text-white focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={() => setAdding(true)}>
          Add a calibration set
        </button>
        <select aria-label="Calibration set" className={input} value={setId} onChange={(e) => setSetId(e.target.value)}>
          <option value="">Calibration set</option>
          {sets.map((s) => <option key={s.id} value={s.id}>{s.question} · {s.counts.labeled ?? s.counts.rows} labeled rows</option>)}
        </select>
        <input aria-label="Label run ID" placeholder="Label run ID" className={input} value={runId} onChange={(e) => setRunId(e.target.value)} />
        <button type="button" disabled={!setId || !runId} className="rounded-md border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-indigo-500"
          onClick={async () => setJobId(await startCompute(runId, setId))}>
          Compute a record
        </button>
        {jobId && <span className="text-xs text-slate-500 dark:text-slate-400" data-testid="compute-status">Calibration job {jobs[jobId] ?? 'queued'}</span>}
      </div>
      {adding && <AddCalibrationSetDialog onClose={() => setAdding(false)} />}
      {groups.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400" data-testid="no-records">No calibration records yet. Add a calibration set, then compute a record for a label run.</p>
      ) : (
        groups.map(([hash, question, list]) => (
          <section key={hash} className="mb-6 space-y-3" data-testid="question-group">
            <h2 className="text-base font-semibold">{question}</h2>
            <TargetEditor question={question} questionHash={hash} />
            {list.map((r) => <RecordCard key={r.id} record={r} />)}
          </section>
        ))
      )}
    </>
  );
}

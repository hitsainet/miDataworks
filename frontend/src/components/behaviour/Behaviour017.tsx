// Feature 017 (FR-017.65; FPRD 017 section 4): the blocks where the operator reads or decides on
// a behaviour detector dataset. Each figure comes from an explicit API value; a missing one reads
// "not measured", never a zero or a pass (FR-017.63). Nothing here names a behaviour: every value
// shown is the record's own.
import type { ReactNode } from 'react';

export const NOT_MEASURED = 'not measured';

const CARD = 'mb-5 rounded-card border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4';
const H = 'text-sm font-semibold mb-2';
const SMALL = 'text-xs text-slate-500 dark:text-slate-400';

const num = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US') : NOT_MEASURED;
const share = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : NOT_MEASURED;
const usd = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? `$${v.toFixed(4)}` : NOT_MEASURED;

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="min-w-44 text-slate-500 dark:text-slate-400">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// --- the version page -------------------------------------------------------------------------

export interface HeldOutProof {
  passes?: boolean;
  columns?: string[];
  held_out_values?: Record<string, string[]>;
  rows?: number;
  generated_rows?: number;
  training_splits?: string[];
  shared_groups?: Record<string, number> | null;
  intersection_count?: number;
  near_values_count?: number;
  rule?: string;
}

export interface BehaviourSplit {
  name: string;
  held_out: boolean;
  rows: number;
  proof?: HeldOutProof | null;
}

export interface RecordedDefinition {
  key: string;
  definition_version: number | null;
  positive_meaning: string | null;
  verified: boolean;
  source: string;
}

export interface CensusCell {
  genre: string;
  placement: string;
  framing: string;
  length_band: string;
  planned: number;
  actual: number;
  shortfall: number;
  short: boolean;
}

export interface Census {
  rows: number;
  planned_rows: number;
  cells_total: number;
  cells_short: number;
  cells: CensusCell[];
  counted?: string;
  templated?: { rows: number; share: number | null; ceiling: number; over_ceiling: boolean };
}

/** "Held-out proof" per value-directed split, and the generated-rows caveat (FR-017.2, .5). */
export function HeldOutProofBlock({ splits, builtRows }: { splits: BehaviourSplit[]; builtRows: number }) {
  const directed = splits.filter((s) => s.proof);
  if (directed.length === 0) return null;
  return (
    <section className={CARD} data-testid="held-out-proof" aria-labelledby="held-out-proof-title">
      <h2 id="held-out-proof-title" className={H}>Held-out proof</h2>
      <p className={`${SMALL} mb-3`}>Proof over {builtRows.toLocaleString('en-US')} built rows: each held-out split's values never appear in the training splits.</p>
      {directed.map((s) => {
        const p = s.proof as HeldOutProof;
        const shared = p.shared_groups ? Object.entries(p.shared_groups) : null;
        return (
          <div key={s.name} className="mb-3" data-testid={`proof-${s.name}`}>
            <div className="text-sm font-medium mb-1">
              {s.name}: {p.passes === true ? 'passes' : p.passes === false ? 'fails' : NOT_MEASURED}
            </div>
            <dl className="stack-y-0.5">
              <Row label="Chosen by">{(p.columns ?? []).join(', ') || NOT_MEASURED}</Row>
              <Row label="Held-out values">
                {Object.entries(p.held_out_values ?? {}).map(([c, v]) => `${c}: ${v.join(', ')}`).join('; ') || NOT_MEASURED}
              </Row>
              <Row label="Rows in the split">{num(p.rows)}</Row>
              <Row label="Generated rows in it">{num(p.generated_rows)}</Row>
              <Row label="Training splits read">{(p.training_splits ?? []).join(', ') || NOT_MEASURED}</Row>
              <Row label="Groups shared with training">
                {shared === null ? NOT_MEASURED : shared.map(([c, n]) => `${c}: ${n}`).join(', ') || 'none named'}
              </Row>
              <Row label="Values close in spelling">{num(p.near_values_count)}</Row>
            </dl>
            {(p.generated_rows ?? 0) > 0 && (
              <p role="note" className="mt-2 rounded-control border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-xs" data-testid={`caveat-${s.name}`}>
                This held-out split holds generated rows. They are held out by their {(p.columns ?? []).join(' and ')} values, which the proof shows never appear in training{p.rule ? ` (${p.rule})` : ''}; published, the version's card says so.
              </p>
            )}
          </div>
        );
      })}
    </section>
  );
}

/** The behaviour definition the version was built against (FR-017.11). */
export function RecordedDefinitionBlock({ definition }: { definition: RecordedDefinition | null | undefined }) {
  if (!definition) return null;
  return (
    <section className={CARD} data-testid="recorded-definition" aria-labelledby="recorded-definition-title">
      <h2 id="recorded-definition-title" className={H}>Behaviour definition recorded with this version</h2>
      <dl className="stack-y-0.5">
        <Row label="Behaviour key">{definition.key}</Row>
        <Row label="Definition version">{definition.definition_version ?? 'not stated'}</Row>
        <Row label="Positive means">{definition.positive_meaning ?? 'not stated'}</Row>
        <Row label="Checked against">{definition.verified ? "miStudio's behaviour catalogue" : 'nothing: supplied by hand, not verified'}</Row>
      </dl>
    </section>
  );
}

/** The carrier census: worst cells first, short cells flagged (FR-017.17, FR-017.58). */
export function CensusBlock({ census, shown = 10 }: { census: Census | null | undefined; shown?: number }) {
  if (!census) return null;
  return (
    <section className={CARD} data-testid="census" aria-labelledby="census-title">
      <h2 id="census-title" className={H}>Carrier census</h2>
      <p className={`${SMALL} mb-2`}>
        {num(census.rows)} rows against a plan of {num(census.planned_rows)} ({census.counted ?? 'rows counted'}); {num(census.cells_short)} of {num(census.cells_total)} cells short of plan. Templated rows: {share(census.templated?.share)} (ceiling {share(census.templated?.ceiling)}).
      </p>
      <table className="text-xs tabular">
        <thead>
          <tr>
            {['Genre', 'Placement', 'Framing', 'Length', 'Planned', 'Built', 'Short by'].map((h) => (
              <th key={h} scope="col" className="px-2 text-left font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {census.cells.slice(0, shown).map((c) => (
            <tr key={`${c.genre}|${c.placement}|${c.framing}|${c.length_band}`} data-short={c.short ? 'yes' : 'no'} className={c.short ? 'text-amber-800 dark:text-amber-300' : undefined}>
              <td className="px-2">{c.genre}</td>
              <td className="px-2">{c.placement}</td>
              <td className="px-2">{c.framing}</td>
              <td className="px-2">{c.length_band}</td>
              <td className="px-2">{num(c.planned)}</td>
              <td className="px-2">{num(c.actual)}</td>
              <td className="px-2">{c.short ? `${num(c.shortfall)} (short)` : '0'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {census.cells_total > shown && (
        <p className={`${SMALL} mt-2`}>The {shown} worst cells of {num(census.cells_total)}. The whole table: dataworks_get_result_download_url (kind census) or Download on the version's results.</p>
      )}
    </section>
  );
}

// --- the generation page ----------------------------------------------------------------------

export interface CarrierRun {
  mode: string;
  counts: Record<string, number>;
  rows_per_run_hour?: number | null;
  generator_model_id?: string | null;
  behaviour_key?: string | null;
}

/** A carrier run's slot checks and throughput (FR-017.14, FR-017.19). */
export function CarrierRunBlock({ run }: { run: CarrierRun }) {
  if (!run.mode.startsWith('carrier_')) return null;
  const slot = run.mode === 'carrier_slot';
  return (
    <section className="mb-3 rounded-control border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="carrier-run">
      <h3 className="text-sm font-semibold mb-1">Carriers</h3>
      <dl className="stack-y-0.5">
        <Row label="Generator">{run.generator_model_id ?? 'not recorded'}</Row>
        <Row label="Behaviour key">{run.behaviour_key ?? 'none'}</Row>
        <Row label="Kept">{num(run.counts['respond:generated'])}</Row>
        {slot && <Row label="Dropped: no slot">{num(run.counts['reason:slot_missing'] ?? 0)}</Row>}
        {slot && <Row label="Dropped: slot twice">{num(run.counts['reason:slot_repeated'] ?? 0)}</Row>}
        <Row label="Rows per run-hour">{num(run.rows_per_run_hour)}</Row>
      </dl>
    </section>
  );
}

// --- the label run page -----------------------------------------------------------------------

export interface StratumCell {
  stratum: Record<string, string>;
  rows: number;
  scored: number;
  agreement: number | string | null;
  kappa?: number | string | null;
}

export interface Strata {
  by: string[];
  cells_total: number;
  cells_not_measured: number;
  cells: StratumCell[];
  next_page_token: string | null;
  coverage: { rows: number; scored: number };
}

/** Agreement with the second opinion, worst stratum first, partial coverage said (FR-017.43). */
export function AgreementByStratumBlock({ strata }: { strata: Strata | null | undefined }) {
  if (!strata) return null;
  const partial = strata.coverage.scored < strata.coverage.rows;
  const fig = (v: number | string | null | undefined) => (typeof v === 'number' ? share(v) : NOT_MEASURED);
  return (
    <section className="mb-3 rounded-control border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="agreement-by-stratum">
      <h3 className="text-sm font-semibold mb-1">Agreement by {strata.by.join(' × ')}</h3>
      <p className={`${SMALL} mb-1`}>
        {num(strata.coverage.scored)} of {num(strata.coverage.rows)} rows scored{partial ? ' (partial: the run stopped before every row)' : ''}; {num(strata.cells_not_measured)} of {num(strata.cells_total)} strata with nothing to measure.
      </p>
      <table className="text-xs tabular">
        <thead>
          <tr>
            {[...strata.by, 'Rows', 'Scored', 'Agreement', "Cohen's kappa"].map((h) => (
              <th key={h} scope="col" className="px-2 text-left font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {strata.cells.map((c) => (
            <tr key={JSON.stringify(c.stratum)}>
              {strata.by.map((b) => <td key={b} className="px-2">{c.stratum[b]}</td>)}
              <td className="px-2">{num(c.rows)}</td>
              <td className="px-2">{num(c.scored)}</td>
              <td className="px-2">{fig(c.agreement)}</td>
              <td className="px-2">{typeof c.kappa === 'number' ? c.kappa.toFixed(3) : NOT_MEASURED}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {strata.next_page_token && <p className={`${SMALL} mt-1`}>Worst {strata.cells.length} strata shown; the rest are on the next page.</p>}
    </section>
  );
}

export interface CostBasis {
  paid: boolean;
  protocol?: string | null;
  price_per_call_usd?: number | null;
  rows_planned?: number | null;
  questions?: number | null;
  estimated_usd?: number | null;
}

/** What a paid run cost, or was planned to (FR-017.40). */
export function CostBasisBlock({ cost }: { cost: CostBasis | null | undefined }) {
  if (!cost) return null;
  return (
    <section className="mb-3 rounded-control border border-slate-200 dark:border-slate-700 px-3 py-2" data-testid="cost-basis">
      <h3 className="text-sm font-semibold mb-1">Cost</h3>
      <dl className="stack-y-0.5">
        <Row label="Paid endpoint">{cost.paid ? 'yes' : 'no'}</Row>
        <Row label="Price per call, in US dollars">{usd(cost.price_per_call_usd)}</Row>
        <Row label="Rows planned">{num(cost.rows_planned)}</Row>
        <Row label="Questions per row">{num(cost.questions)}</Row>
        <Row label="Estimated cost, in US dollars">{usd(cost.estimated_usd)}</Row>
      </dl>
    </section>
  );
}

// --- the approvals card -----------------------------------------------------------------------

export interface PaidRunFacts {
  rows?: number;
  cap_rows?: number;
  estimated_usd?: number;
  price_per_call_usd?: number;
  spent_usd?: number;
  ceiling_usd?: number | null;
  text_leaves?: string;
}

/** A paid classifier run waiting for the operator: rows, cap, cost and where the text goes. */
export function PaidRunCard({ facts }: { facts: PaidRunFacts }) {
  return (
    <div className="mt-1 text-xs text-slate-700 dark:text-slate-300" data-testid="paid-run-card">
      <div>
        {num(facts.rows)} rows (cap {num(facts.cap_rows)} per run) at {usd(facts.price_per_call_usd)} a call: about {usd(facts.estimated_usd)}.
      </div>
      <div>Spent so far {usd(facts.spent_usd)} of a ceiling of {usd(facts.ceiling_usd)}.</div>
      {facts.text_leaves && <div className="mt-0.5 font-medium text-amber-800 dark:text-amber-300" role="note">{facts.text_leaves}</div>}
    </div>
  );
}

// --- the lists' behaviour filter --------------------------------------------------------------

const SLUG = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;

/** A plain behaviour-key input beside a list's other filters (FR-017.53, FR-017.65). */
export function BehaviourFilter({ value, onChange, testId }: { value: string; onChange: (key: string) => void; testId: string }) {
  const bad = value !== '' && !SLUG.test(value);
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="text-slate-500 dark:text-slate-400">Show only behaviour</span>
      <input
        data-testid={testId}
        value={value}
        placeholder="any behaviour"
        aria-invalid={bad}
        onChange={(e) => onChange(e.target.value.trim())}
        className="w-44 rounded-control border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-xs focus-visible:ring-2 focus-visible:ring-indigo-500"
      />
      {bad && <span role="alert" className="text-red-600 dark:text-red-400">Lower case letters, digits and hyphens</span>}
    </label>
  );
}

export const isBehaviourKey = (value: string): boolean => SLUG.test(value);

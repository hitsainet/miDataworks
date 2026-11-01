# Synthetic generation and steered pairs (feature 007)

Feature 007 adds generated rows where real data is thin, without letting a model grade itself,
narrow the data, leak test rows, or claim a steering it did not apply. It is the **Generation**
screen, a slot on **Version detail**, a link in the guided flow's Assemble step, eighteen REST
routes and eighteen MCP tools (category `generation`).

The screen's captures are written by `frontend/e2e/generation.spec.ts` in both colour modes:
`e2e/captures/{dark,light}-generation-detail.png`, `-generation-new-run.png`,
`-generation-refusals.png` and `-version-diversity.png`.

## Reading a run at a glance

Each run tile leads with what the run is for: its kind and the dataset it reads, by name and
version number ("Minimal pairs from highstakes-chat v3"; the version id is in the tooltip), then the
behaviour the data is for. The behaviour is read from the records that already name one: a detector
set built from the same dataset (its behaviour key and what a positive label means), or the rubric
of the minimal-pair chain the run belongs to. When neither exists the tile says "No behaviour set";
give a behaviour key to the detector set or the rubric to fill it in.

The next lines say when (started and ended in US Eastern time with the total duration, "Running for
…" while it runs, "Queued …, waiting …" before a worker picks it up), how (generator model, template,
seed rows and who started it) and the outcome (generated, discarded with the commonest reasons,
pairs, and the candidate or pair version it produced). Opening a run shows the same at greater
length: every behaviour found and where, the queue wait, working time and attempts, the template's
sampling, the judge, the steering and the seed split.

"Revision not reported" means the model server did not say which commit of the model it served, so
rebuilding the data later is less certain. miLLM reports the revision recorded on its model row,
also for a model that is not loaded when the run is planned; a model registered without one stays
"not reported". The list filters by status and behaviour and sorts newest first.

## How generated rows reach a version

A **generation run** calls the model and records what came back; a **version build** never calls a
model. Three ordinary versions per synthesis:

1. **Input version V** — must already have a held-out split (`HELD_OUT_MISSING` otherwise, naming
   the `split` operator as the next step). Seed rows come only from the seed splits you name, never
   from a held-out split (`HELD_OUT_SEED`), and only from rows whose origin is `source`.
2. **Candidate version C** — **Build candidate version** on a completed run. A system recipe runs
   `dw_generated_rows`, which adds every `generated` record of the run, reading only the chunks the
   run committed (each checked against its SHA-256) and refusing a run the build did not bind. Each
   added row carries `_dw_origin = generated`, its prompt and seed keys as parents, the seed row's
   split, and the metadata columns `generation_run_id`, `generation_record_index`,
   `generation_side` and `generation_model_id`.
3. **Filtered version F** — a recipe over C with any of `dw_judge_filter` (keep the verdicts you
   name, or a lowest score, from a judge run over the generated rows), `dw_pair_filter` (keep a
   steered pair only when a pairwise judge agrees with the chosen side in both orders),
   `dw_pair_from_scores` (one prompt/chosen/rejected row per prompt from the best- and worst-scored
   responses) and 004's `cluster_balancer` with `applies_to: generated` (default cap: the largest
   cluster among the source rows). Each touches only generated rows and records why each one left.

## What is checked on every response

Every request carries `X-miLLM-Strict: true` and `X-miLLM-Load-Policy: refuse` (miDataworks never
loads a model), the lease header when the run is pinned, a per-response seed, and the side's
steering. An unsteered side on miLLM sends the explicit empty set `{"steering": {"features": []}}`,
so a globally active profile cannot steer it silently.

miLLM's `X-miLLM-Steering` header is compared with what was asked for: kind, profile name and
source (`request`, not `active`), intensity (as a number), SAE, layer, feature count and the
steering-set hash (miLLM's canonical form, pinned by its four published test vectors). A response is
recorded `generated` only on a match; otherwise it is `discarded` with its reason
(`steering_mismatch`, `steering_unreported`, `parse_failure`, `empty_response`) or `skipped`
(`context_overflow`, `row_error`). A missing header is recorded as absent and read "not reported",
never "unsteered". A `changed` flag or a `clamped` count is a mismatch.

A run whose endpoint is not miLLM records `not_applicable` and is `pinned = false`; steering is
refused there (`STEERING_UNSUPPORTED`).

## Steered pairs

Two settings — a miLLM profile by name, an inline feature set, or unsteered — must differ on exactly
one SAE feature of one SAE and layer (`NOT_ONE_AXIS` lists every differing index otherwise; one
cluster profile at two intensities moves every member and is refused). Both sides get the same
prompt, template, sampling and seed. A pair is kept only when **both** sides report the requested
steering; one mismatched side voids the pair. A profile is re-read before each chunk; if its
`updated_at` moved, the run fails with `profile_changed` and cannot be resumed.

miDataworks never creates, edits, activates or attaches anything in miLLM: a missing profile is
`PROFILE_NOT_FOUND`, a missing SAE is `SAE_NOT_ATTACHED`.

## Carrier runs (feature 017)

Two modes generate a behaviour detector's carriers from a **carrier plan** version (`carrier_plan@1`,
see [Versions](versions.md#behaviour-builds-definition-plan-and-census)), one carrier per plan row:

- `carrier_slot`: the model writes the text around a **slot token** (one per run, `[[SLOT-…]]`,
  given to the template as `{slot}` where the plan row has a slot). A carrier that holds it zero
  times, or more often than its plan row has a slot, is dropped and counted (`slot_missing`,
  `slot_repeated` in the run's counts); the element itself is inserted later, by code.
- `carrier_instructed`: the model writes the whole text from the row's planned cells (genre,
  framing, length band and each `planned_<sub-question>` are template placeholders).

A carrier run names its **generator** (`generator_model_id`), sent as each request's `model`: one
run per generator, and the endpoint must serve it (`MODEL_NOT_LOADED` on miLLM,
`generator_not_served` elsewhere). Its templates come from a **template map** (`{"column": "genre",
"templates": {<value>: <respond template id>}}`); a plan value with no template is refused
(`template_unmapped_value`). The run takes **every** model-route plan row once, in row-key order
(leave `sample_size` out); rows marked `generator = template` are left to the synthesiser route.

**The template check.** A carrier template must never show the element: it is refused
(`slot_template_has_element`, naming the rule set and signal, or the phrase) when a signal of the
rule sets the run pins (`rule_sets`) fires on its text, system text included, or it holds a phrase
of the plan's declared phrase list. A template can be checked when it is saved too
(`check_rule_sets`, `check_phrases`).

**A version from carriers.** `dw_generated_rows` writes each carrier into the plan's text column
and copies every other plan column from its plan row; `generator` is the model that wrote it and
`generator_revision` its revision ("not reported" when the server gave none); `slot` becomes the
run's token. A version built from two runs records each row's own generator.

**Plans, resumes and top-ups.** The plan answer states the templates per value, the plan rows the
run takes and `estimate`: rows and run hours at the rate this behaviour's earlier carrier runs with
this generator achieved, or "not measured" (never zero) before one. A stopped run resumes without
sending a completed row again. A **top-up** (`responds_to: {version_id, cells}`) generates only the
named census cells; the version built from it names the earlier version and the cells in its step
report. `GENERATION_REQUESTS_IN_FLIGHT` (1 until measured) is how many plan rows' requests a
carrier run on the native engine path keeps in flight at once; the value is recorded on the run when
it starts (`requests_in_flight`; 1 for every other run and for the relay path, which send one at a
time). Rows are still decided and committed in plan order, so the records do not depend on it.

## Judge independence, in three places

A model may not grade its own output. Identity is (model, revision or "not reported", steering
hash or "none"); the endpoint URL is ignored, and two "not reported" revisions of one model
conflict. The check runs when a run is planned and started, in the worker after the first response
(with the model that actually answered), and in 005's label-run preflight for any judge **or
classifier** run over a version that holds a run's generated rows (`JUDGE_IS_GENERATOR`). When the
generation endpoint inherits the judge's, the message says so.

## The diversity report

On Version detail, for a version that binds a generation run: distinct-1, distinct-2, embedding
spread (mean cosine distance to the centroid), cluster coverage and the largest cluster's share, each
with a 95% interval, against the nearest ancestor that holds no generated row. Intervals come from
half-size subsamples of both sides at one size (a with-replacement bootstrap lowers distinct counts by
construction). A figure **falls** only when its whole interval is past the reference's.

Two controls must pass or the verdict is `invalid`. The negative control: a reference sample
half-replaced by copies from its largest cluster (and, for spread, by one row's nearest neighbours)
must fall. The positive control: the reference is split into two random halves eight times, and on
each figure the halves must not differ by more than the figures' own intervals allow. The control
fails a figure when the average squared difference, in standard errors, passes a bound that random
halves of a correctly measured figure pass on 0.1% of reports (the check's `statistic` shows each
figure's average, the bound, and how many splits fell). Coverage moves in whole clusters, so one
cluster's difference is not counted.

Every report records its **method version** (`method_version` on the report, and in `method`).
Version 1 asked one split of the reference not to fall, which 0.4–0.8% of reports failed on noise alone
and read `invalid`; version 2 (2026-10-19) fixed that. Version 3 (proposed 2026-10-19, pending the
operator's decision) changes how the **largest-cluster share** is measured: the clusters are fitted on
the distinct texts of both samples, instead of on the reference alone. On the reference's own clusters,
new rows that resemble no cluster all landed in the most diffuse one, so the share "fell" for most
versions that only added fresh rows like the reference's (70% on a joke corpus); on the shared fit it
did not (0 in 600), and a version where 30% of the rows are near-copies of one text still falls (87%).
The share now only rises when a concentration outgrows the reference's own largest cluster; smaller
repetition shows in distinct-1 and distinct-2. Cluster coverage and every other figure are unchanged.
A report is kept with the method it was written with. To get a report at the current version, run the
report again from the version; the new report is added and becomes the one shown. Without an embeddings endpoint, spread is `not measured` and clusters are
lexical. A `falls` verdict warns ("Cap the largest cluster or generate from more seed rows") and
refuses nothing: the publish checks add the note `diversity_falling` to the card. A Beat sweep
queues a report for every completed version with generated rows that has none. It does not queue
it again after a refusal (for example, the version and its reference cannot be compared), and it
stops after `DIVERSITY_SWEEP_MAX_ATTEMPTS` unexpected failures (an unreachable embeddings endpoint);
run the report from the version to try again. Rows whose text is empty are not sampled (an
embeddings endpoint refuses an empty input); the report records how many it left out on each side
(`method.blank_rows_excluded`).

### Breakdown by generator and by genre

`breakdown_by` (for example `["generator", "genre"]`, at most four columns the version has) adds
`figures.breakdown`: for each value of each column, its rows against the rows of every OTHER value,
at one subsample size: distinct-1, distinct-2 and lexical cluster coverage (spread is not measured
per group). A value whose whole interval lies below the others' `falls` (`falling` names the
figures). A version made only of carriers has no ancestor without generated rows: the report reads
"reference: not measured" and the breakdown still compares its generators and genres with each
other. A column the version lacks is refused before any job (`breakdown_column_missing`).

## Settings

`GENERATION_ENGINE_PATH` (`native` by default; `relay` runs 007's operator on 003's loopback relay),
`MILLM_STEERING_SUPPORTED` (`true` since miLLM serves feature 028), `GENERATION_CHUNK_SIZE`,
`GENERATION_PREVIEW_MAX_PROMPTS`, `GENERATION_PREVIEW_TIMEOUT_SECONDS`, `GENERATION_MAX_N_RESPONSES`,
`DIVERSITY_SAMPLE_SIZE`, `DIVERSITY_BOOTSTRAP_RESAMPLES`, `DIVERSITY_SWEEP_SECONDS`, `DIVERSITY_SWEEP_MAX_ATTEMPTS`,
`DIVERSITY_CLUSTER_K`, `GENERATION_REQUESTS_IN_FLIGHT` (see `.env.example`).

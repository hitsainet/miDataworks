# Synthetic generation and steered pairs (feature 007)

Feature 007 adds generated rows where real data is thin, without letting a model grade itself,
narrow the data, leak test rows, or claim a steering it did not apply. It is the **Generation**
screen, a slot on **Version detail**, a link in the guided flow's Assemble step, eighteen REST
routes and eighteen MCP tools (category `generation`).

The screen's captures are written by `frontend/e2e/generation.spec.ts` in both colour modes:
`e2e/captures/{dark,light}-generation-detail.png`, `-generation-new-run.png`,
`-generation-refusals.png` and `-version-diversity.png`.

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

## Settings

`GENERATION_ENGINE_PATH` (`native` by default; `relay` runs 007's operator on 003's loopback relay),
`MILLM_STEERING_SUPPORTED` (`true` since miLLM serves feature 028), `GENERATION_CHUNK_SIZE`,
`GENERATION_PREVIEW_MAX_PROMPTS`, `GENERATION_PREVIEW_TIMEOUT_SECONDS`, `GENERATION_MAX_N_RESPONSES`,
`DIVERSITY_SAMPLE_SIZE`, `DIVERSITY_BOOTSTRAP_RESAMPLES`, `DIVERSITY_SWEEP_SECONDS`, `DIVERSITY_SWEEP_MAX_ATTEMPTS`,
`DIVERSITY_CLUSTER_K` (see `.env.example`).

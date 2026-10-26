# Label runs and the Label step (feature 005)

Feature 005 labels the rows of a completed version with a model and records who labelled what,
with which model, revision, question and thresholds. It covers the guided flow's **Label** step,
the **Label runs** screen (with its **Rubrics** library), the four endpoint role cards in
**Settings**, thirty REST routes and twenty-three Model Context Protocol (MCP) tools in the category `labeling`. The tool
that sets a role's API key, `dataworks_set_endpoint_key`, sits in `settings`.

The MCP tools are `dataworks_list_endpoint_roles`, `dataworks_fetch_models`,
`dataworks_test_endpoint_role`, `dataworks_list_templates`, `dataworks_save_template`,
`dataworks_get_template`, `dataworks_clone_template`, `dataworks_list_rubrics`,
`dataworks_save_rubric`, `dataworks_get_rubric`, `dataworks_clone_rubric`, `dataworks_label_sample`,
`dataworks_preview_band`, `dataworks_get_keep_share`, `dataworks_plan_label_run`,
`dataworks_start_label_run`, `dataworks_list_millm_probes`, `dataworks_list_label_runs`,
`dataworks_get_label_run`, `dataworks_get_label_run_labels`, `dataworks_get_label_run_agreement`,
`dataworks_resume_label_run`,
`dataworks_rederive_label_run` and `dataworks_aggregate_judges`.

Captures, in both colour modes under `e2e/captures/{dark,light}-`: `frontend/e2e/labeling.spec.ts`
writes `label-step.png`, `label-step-probe.png`, `label-runs.png` and `settings-roles.png`;
`reproduction-link.spec.ts` writes `label-step-link.png`, `label-step-linked.png`,
`label-step-linked-served.png` and `label-step-no-millm.png`; `live-findings.spec.ts` writes
`label-step-gate-failed-before.png`, `label-step-gate-retry.png` and
`label-run-gate-failed-no-resume.png`.

## Endpoint roles

Settings holds one card per role, never a list of endpoints: **classifier**, **judge**,
**generation** and **embeddings**. Each role has a protocol, a URL, a model and an optional key.
The key is encrypted at rest and is only ever shown masked.

A key belongs to the server it was entered for: its scheme, host and port. miDataworks sends it to
that server and to no other, on every path (labeling, generation, embeddings, Fetch models, Test
endpoint, Data Designer steps). Each role keeps **one key per server**. If you point a role at
another server, the key you were using is kept for its own server and is never sent to the new one;
if you point the role back, that key is used again without being entered again. For example, a
classifier moved from the TypeSafe API to miLLM sends miLLM no key (unless you enter miLLM's own),
and moved back to the TypeSafe API it sends the TypeSafe key again. After a move, Settings says what
happened to the keys. A change of path on the same server (`/v1` to `/v2`) keeps the key in use.

Under **Stored keys** each role card lists the servers it holds a key for, masked, marking the one in
use; **Delete** removes the key for one server. Entering an empty key clears only the key for the
server the role points at now.

| Role | Protocols | Notes |
|---|---|---|
| classifier | `openai_scoring`, `tei_classification`, `plugin` | its own endpoint |
| judge | `openai_chat` | **Use**: its own endpoint, the classifier's URL and key (keeping the judge's model), or none |
| generation | `openai_chat` | the judge's URL, key and model unless you set its own |
| embeddings | `openai_embeddings`, `tei_embeddings` | the judge's URL, key and model unless you set its own |

**Fetch models** lists what the server serves: `/v1/models`, or `/info` on Text Embeddings Inference
(TEI). **Test endpoint** sends one minimal request and reports the server kind: miLLM, TEI or
OpenAI-compatible. For miLLM it also reports the loaded model, the lease state and the
queue depth. A role with no URL or no model refuses with `ROLE_UNCONFIGURED`, naming the Settings
field to fill.

## Templates and rubrics

A **decision template** tells a classifier run what to send and how to read the answer. It holds
the input fields, the label set and, for scoring, the answer token IDs. A template that names a
model is bound to it. Token IDs mean different tokens on another model, so a bound template
refuses an endpoint serving another model (`TEMPLATE_MODEL_MISMATCH`, naming both). A template
whose protocol differs from the classifier role's protocol refuses with `PROTOCOL_MISMATCH`. Two
built-in templates are seeded: `jev/noul-bare-v1` (JEV-9B) and `jevify/yes-no-v1`, bound to
miLLM's `jevify-gemma4-e4b` at native bfloat16 (completions scoring; the run's question is asked
with "Answer Yes or No.", and P is the probability of "yes").

A **rubric** tells a judge run what to ask. It has a style (`pointwise`, `pairwise`, `binary` or
`stepwise`), allowed verdicts and a parser (`verdict_line_v1` or `json_v1`). Both kinds are
versioned and hashed. To change one, clone it, which saves the next version under the same name.
An import is validated before it is saved: unknown keys are refused, and the same name and version
with another body is refused. A template or rubric that any run references can never change
(`TEMPLATE_IMMUTABLE`).

A rubric may carry a **behaviour key**: the name of the behaviour it judges, as miStudio's
behaviour catalogue names it (for example `pii` or `high-stakes`; lower case letters, digits and
hyphens). The key is not part of the rubric's hash, so it does not change the version. It is set
when the rubric is saved or cloned, changed on an unused rubric with
`PATCH /api/v1/rubrics/{id}` (or **change** in the rubric list), and fixed once a run has used the
rubric: clone it to give the same text another key. The export carries it as `behaviour`; the
format is published as `docs/schemas/midataworks-rubric-v1.json`. Importing a stored rubric with a
different key is refused (`RUBRIC_BEHAVIOUR_CONFLICT`).

## Protocols

| Protocol | What one row costs | What it records |
|---|---|---|
| `openai_scoring` | one completions or chat request with `max_tokens: 1` and logprobs restricted to the template's token IDs | a probability per label |
| `tei_classification` | `POST /predict` with `truncate: false`; the template maps the model's labels to the run's | every raw score |
| `plugin` | a `midataworks.classifiers` entry point, imported only when it is on the Operators allowlist | `plugin_not_allowed` or `plugin_invalid` when it is not allowed or not a classifier |
| `openai_chat` (judge) | rubric-rendered messages, temperature 0 unless set, the run's seed | verdict, rationale, raw output |
| `millm_probe_score` (role `probe`) | `POST /api/probes/score` on miLLM, one input per call | miLLM's verdict, score and threshold |
| `millm_sae_features` (role `features`) | one scoring-mode `POST /v1/completions` with `return_sae_activations` | outcome `tagged` with the sparse autoencoder (SAE), layer and top features |

A judge asks for `response_format: json_schema` only when one probe request at run start shows that
the server honours it. Otherwise it parses strictly. A seed counts as confirmed only when miLLM
echoes it; otherwise the run records "seed not confirmed". A pairwise rubric is asked in both
orders: if the two answers disagree, the outcome is `position_inconsistent`. A judge run stops with
`PARSE_FAILURES` when more than 5% of its answers fail to parse, counted after 200 rows.

**Feature tagging** has no Label-step option. Start it through REST or `dataworks_start_label_run`
with `role: "features"`. The SAE must be attached in miLLM (`SAE_NOT_ATTACHED`), and miLLM must read
it unsteered (`READ_POINT_MISMATCH`).

**Probe verdicts** score with a probe imported in miLLM, at a window of `all`, `prompt`, `response`
or `last_user`. The probe's own bar decides, so the run takes no template, question or thresholds.
The probe list names each probe by what it detects, in miLLM's words: the training view's label
mapping as "`<positive>` vs `<negative>`" (or miStudio's `concept` sentence), then the model and the
layer, then what sets it apart: its AUROC on miStudio's in-distribution evaluation and when it was
trained (US Eastern), then its name. A probe whose definition states neither reads "what it detects
is not stated". When two or more probes detect the same thing on the same model and layer, the one
with the highest AUROC is marked **recommended** and the latest import **newest**; under the picker,
the chosen probe's evaluation, training dataset, date, miStudio run, import date and bar are listed.
These facts come from miLLM's probe list (`created_at`, `target_fpr`) and each probe's definition
(`provenance`, `evidence.evaluations`); a fact the definition does not state reads "not stated".
The plan opens with a plain-language summary (what it detects, how a row is decided, and
the reproduction check in one sentence); the probe facts, the preflight and the reproduction detail
are under **Details**.
Before labelling, the run must pass the reproduction gate described in
[Detector sets](detector-sets.md).

## The Label step and starting a run

Choose a version, choose **Label with** (the classifier, the judge or a probe in miLLM), choose a
template or rubric, and name the column. A classifier run also needs the **Question**, which is
asked of every row exactly as written, and both thresholds:

- **Thresholds start empty and are required on every run** (`THRESHOLDS_INVALID` names the field).
  A row is positive when its probability is at or above the positive threshold. It is negative at
  or below the negative threshold, which must be lower. Any row in between is `excluded`, kept with
  its probability and never guessed. A label set of more than two labels needs a minimum top
  probability instead.
- **Try it on a sample** labels up to 20 rows now and writes nothing. **Estimate keep share** scores
  a random sample (400 rows by default, at most 1,000) and states the share kept with a 95%
  interval and the sample size. Neither takes a lease.

The plan, also available as `GET /api/v1/label-runs/plan`, is a dry run. It reports the rows the run
covers, the rows reused, the rows to label and whether an agent's start would wait for approval.
The button names the count: **Label N rows**. Until the plan has counted, or while the run cannot
start, it names the most the run can label (the cap, else the version's rows), and the reasons are
listed above it, each with what to do (for example "Load meta-llama/Llama-3.1-8B-Instruct in miLLM,
then start the run").

**Waiting for a model in miLLM.** When the plan is refused with `MODEL_NOT_LOADED` or
`MODEL_NOT_RESIDENT`, or miLLM reports nothing loaded, the step names the model to load and links to
miLLM's Models page at `MILLM_UI_URL` (the browser's address of miLLM's UI, served in
`GET /api/health` as `dependencies.millm.ui_url`; unset, the step names the setting instead of a
link). It then re-reads miLLM every 5 seconds, whenever the tab comes back into view, and on
**Check again**, so a model loaded in miLLM's own UI unblocks the start with no reload. In the guided
flow every labeler choice (labeler, probe, window, column, row cap, template, question, thresholds)
is kept on the draft, so a reload starts from them.

**After Start** the guided flow stays on the Label step and follows the run: rows done of total, the
rate and the time left, then the result. **Continue to Assemble** moves on; the run keeps going. A
second start is refused on screen while the followed run is live, and one click starts one run.

**Against the label column in the data.** A finished classifier, probe or re-derived run is compared
with a column its input version already has (`GET /api/v1/label-runs/{id}/agreement`, MCP
`dataworks_get_label_run_agreement`). The candidates are the version's own columns holding exactly
two values, never `_dw_*` bookkeeping or the text the labeler read; booleans come first, and the
reader can choose another. The positive value is taken only when it is unambiguous (a boolean's
`true`, a 0/1 column's `1`, yes/no, positive/negative, or the run's own label names); otherwise the
answer asks for `positive_value`. Over the run's positive and negative labels it reports the four
counts, the agreement, each side's positive share and, when every compared row has a score, the
AUROC from the run's own probability or probe score, with a note in plain words ("close", "partial"
or "weak" agreement). The column is the dataset's own label, not a checked ground truth.

**The version list** shows completed versions, newest first (`GET /api/v1/versions?order=recent`),
with a search box once there are more than eight. By default it lists each dataset's newest version
and leaves out test builds (a dataset name with the word test, tests, e2e, smoke, scratch or tmp);
**Show N older and test versions** lists the rest, and a search always reaches every version. In the guided flow the version the journey just
built is chosen for you and marked "this dataset".

**Rows to label (the row cap).** `row_filter.max_rows` caps the run at that many inputs (empty: every
row). The capped inputs are the ones with the **lowest row keys** after any origin filter, a fixed
subset that does not depend on file order (a row key is a content hash, so the subset is
unrelated to the order the source listed its rows). The cap is applied before reuse is counted and
before already-recorded rows are removed, so the plan, an agent's approval count (P-07), the run and
every resume cover the same inputs: a cancelled run resumes inside the cap, never past it. The run
records the cap in its `row_filter`, and its card reads "capped at N". The MCP tool takes the same
`row_filter`.

**Row coverage.** A row key is the row's content, so identical inputs share one key and one label.
When the version holds copies, the plan reads "N rows, M distinct inputs" and says that the label
applies to every copy. If copies disagree in their metadata, it says so. If the plan's count and the
coverage count ever differ, the start refuses with `ROW_COUNT_MISMATCH` instead of recording either.

## How a run proceeds

A run resolves its role again, sends one request per row in row-key order, and commits labels in
chunks of 200 rows. It stops with `ENDPOINT_CHANGED` when the role's URL or model changed in
Settings since the run started.

- **Backpressure** (503) waits for `Retry-After` and never counts against the row.
- A row that does not fit the model's context is `skipped` after one request.
- Twenty consecutive row failures stop the run with `ENDPOINT_FAILING`. Committed chunks are kept.
- A response that names another model, or a change in miLLM's `system_fingerprint`, stops the run
  with `MODEL_CHANGED`. A TEI identity is checked again at every chunk boundary.
- `STRICT_REFUSAL` is a defect in miDataworks; the run fails and is not retried.

**Cancel** stops a queued or running run (`RUN_NOT_CANCELLABLE` otherwise). **Resume** starts a new
job on a cancelled or failed run from its last committed chunk (`RUN_NOT_RESUMABLE` otherwise). A
run the reproduction gate stopped is not offered Resume (`RUN_GATE_FAILED`).

**Timing.** A run reports `timing`: `active_seconds` is the sum of each finished job's
started-to-completed time (queue waits and the gap between a cancel and its resume are not working
time), `rows_scored` is the rows sent to the labeler (`rows_done - rows_reused`), and
`rows_per_second` their ratio. A finished run's card reads "took 20m 50s · 20.0 rows/s"; a running
one shows the live rate. Cards name the input version by dataset and number
(`input_dataset_name`, `input_version_number`). A run's progress is rows done over rows covered:
the run's room carries `progress` with each `label_run:progress` event, and the job's own room gets
`job:progress` with the same figure and the row counts, so the Operations drawer reads
"16,400 of 25,000 rows · 66% of the job" (the job row is also written, at most once a minute). On a wide screen the run's detail stays in view beside the list; on a narrow
one, opening a card scrolls to it.

**Reuse.** A run copies a label that an earlier run made under the same fingerprint. The fingerprint
is the labeler identity plus sampling, structured-output mode and packing. Thresholds are not part
of it, so the copied probabilities are decided again under this run's thresholds. Reuse happens
only when **both** runs reported a model revision. A generic OpenAI-compatible server reports none,
so its runs always label every row.

**Re-derive** (`POST /api/v1/label-runs/{id}/rederive`) makes a child run from a completed
classifier run's stored probabilities, with new thresholds and no endpoint call. The parent never
changes (`RUN_NOT_REDERIVABLE` for any other run). **Aggregate** combines two or more completed judge
runs on one version. A unanimous verdict wins; a majority wins and is flagged; a tie is excluded and
flagged (`RUN_NOT_AGGREGATABLE`, `AGGREGATE_DUPLICATE`). A judge or classifier run over generated rows refuses with
`JUDGE_IS_GENERATOR` when its model generated them (see [Generation](generation.md)).

## miLLM: the lease, load policy and batch transport

Every request for scoring, judging or generation carries `X-miLLM-Load-Policy: refuse`, and every
request to an OpenAI-compatible endpoint carries `X-miLLM-Strict: true`. **miDataworks never loads or
swaps a model.** When the model a run needs is not loaded, the plan refuses with `MODEL_NOT_LOADED`
and names the model to load. It does not refuse when a miDataworks job is already queued or running
on the loaded model; then the new job waits in the queue.

On miLLM, a run joins the shared **model lease** (holder `midataworks`, time-to-live 1,800 s) without
an approval: taking a lease loads nothing. The lease is renewed at chunk boundaries and released by
the last member. The lease ID travels only in `X-miLLM-Lease`, never in a log line or a response.
If another holder has the lease, the plan refuses with `LEASE_HELD`; a queued job waits 60 s and
tries again. A lost lease stops the run at a chunk boundary with `LEASE_LOST`, and the run can be
resumed. A miLLM without the lease routes runs **unpinned**, and the card shows a badge.

The **batch transport** (`transport: "batch"`) sends classifier scoring as a miLLM batch of up to
50,000 lines, under the run's lease, with packing off. It serves `openai_scoring` on miLLM only
(`BATCH_UNSUPPORTED`).

## The agent labelling window

An agent's run counts its rows to label (reused rows excluded) against its input version over a
24-hour window. Recipe builds that label rows count against the same budget. A start that would take
the version **over 5,000 rows** (`AGENT_LABEL_ROW_THRESHOLD`, a strict "over") waits for one
`agent_label_rows` approval. The approval binds the planned rows. If the run would now label more
rows, the approved start refuses with `PLAN_CHANGED`. A start that needs approval but carries none
refuses with `APPROVAL_REQUIRED`. A resume needs no approval because it re-sends no counted row
(except on a paid endpoint, below).
Runs an operator starts are not counted.

## Second opinions and paid classifiers (feature 017)

A behaviour dataset's labels come from declared rules; a classifier gives a **second opinion** on
a selection of its rows.

- **The selection.** `sample@1.1.0` with `mode: stratified` keeps up to `per_stratum` rows of
  every combination of the `strata` columns (genre, generator, kind, label ...), every row whose
  `always_keep_column` is true (for example `label_conflict`), and the rows whose
  `instructed_column` holds one of `instructed_values` at their own share: `instructed_rate` of
  them, at least `instructed_min` (all when fewer). Left empty, the build fills the rate and the
  minimum from `SECOND_OPINION_INSTRUCTED_RATE` (0.10) and `SECOND_OPINION_INSTRUCTED_MIN` (500)
  and records them with the step. Every row left out is in the drop log with its stratum and rank.
- **One run per question.** Each run asks one question, worded as the behaviour's data, and names
  the column it is compared with (`target_column`: the label or one `sq_<name>`, never
  `deployed_truth`, which applies code's veto). Runs of one second opinion share a `handoff_id`.
- **Paid endpoints.** Enter the price per call (one row, one question, in US dollars) on the
  endpoint role in Settings (`price_per_call_usd`; PIN-protected; an agent's write waits for you as
  `config_write`; a move to another server clears it). A role with a price above zero is paid; a
  hosted protocol (`typesafe_systemone`) with no price is treated as paid until you enter one,
  zero included. The plan shows the cost: rows x questions x price, the per-run cap, the ceiling
  and what paid runs have spent. A paid run is refused, for you and for agents, while unpriced
  (`classifier_unpriced`), while `CLASSIFIER_SPEND_CEILING_USD` is unset or would be passed
  (`classifier_spend_ceiling`; only you can set it) and over `CLASSIFIER_RUN_CAP_ROWS` (2,500;
  `classifier_cap_exceeded`). **Every agent run on a paid endpoint waits for your approval**
  (`paid_classifier_run`), whatever its size; the card names the rows, the cap, the cost and the
  server the rows' text is sent to. What you approve binds the run: if the role's server changes
  or its price rises before you approve, the approved start (or resume) is refused `PLAN_CHANGED`
  and the agent asks again. "Spent", for the ceiling, also counts what queued and running paid
  runs are still planned to score, so two runs that each fit cannot together pass it.
- **Previews on a paid endpoint.** "Try it on a sample" and the keep-share estimate send rows'
  text too. On a paid role an agent's preview is refused (`paid_classifier_preview_refused`; it
  plans a label run instead, which waits for you); yours is refused like a run while the role is
  unpriced or the ceiling is unset or would be passed. A preview's calls are not yet counted in
  "spent".
- **A run that stops part way** keeps every row it scored and records why (`stop_reason`:
  `endpoint_failed`, `cap_reached` when the cap or the ceiling is reached at a chunk boundary, or
  `cancelled`). Its agreement reads `partial` with each stratum's coverage. An agent's resume of a
  paid run waits for your approval of the remaining rows only.
- **Agreement by stratum.** `GET /label-runs/{id}/agreement` (MCP
  `dataworks_get_label_run_agreement`) compares the run with its `target_column` overall and per
  stratum of `by` (default genre, generator, kind and form where the version has them): rows,
  scored, coverage, confusion counts, agreement, Cohen's kappa and AUROC, worst cells first, paged
  with `limit` and `page_token`. A stratum with nothing compared reads "not measured".
- **Disagreements go to review.** `POST /label-runs/handoffs/{handoff_id}/review` (MCP
  `dataworks_review_disagreements`) opens label-review queues over exactly the rows where the
  hand-off's runs disagree with their column, at most 5,000 rows a queue, ordered so every stratum
  is in the first queue, in one step; more queues than `REVIEW_MAX_QUEUES_PER_HANDOFF` (10) is
  refused. Agents may open them; only your decisions change the published label.
- **Independence.** `dataworks_check_judge_independence` with `role: classifier` and
  `generator_run_ids` compares the classifier with the generators those runs recorded. An
  endpoint that does not disclose its model (TypeSafe answers through an alias) reads "not
  reported", never independent.

## Without miLLM

Classifier, judge, keep-share and sample work against any OpenAI-compatible or TEI endpoint, which
then runs unpinned and records "revision not reported". Only the miLLM actions refuse:

- probe verdicts and feature tags refuse with `PROBE_ENDPOINT_UNCONFIGURED` when `MILLM_BASE_URL`
  is not set, and with `PROBE_ENDPOINT_NOT_MILLM` when it answers as another server;
- the Label step marks the probe option "(miLLM not configured)" and says every other labeller
  works without it;
- batch transport refuses with `BATCH_UNSUPPORTED`.

## Settings

`LABEL_CHUNK_SIZE` (200), `LABEL_MAX_CONSECUTIVE_FAILURES` (20), `LABEL_SAMPLE_MAX_ROWS` (20),
`KEEP_SHARE_SAMPLE_ROWS` (400), `JUDGE_PARSE_FAILURE_STOP_SHARE` (0.05), `MILLM_LEASE_TTL_SECONDS`
(1800), `LABEL_BATCH_MAX_LINES` (50000), `AGENT_LABEL_ROW_THRESHOLD` (5000),
`AGENT_LABEL_WINDOW_HOURS` (24) and `MILLM_BASE_URL`; the rest are in `.env.example`.

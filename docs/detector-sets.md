# Detector sets and the miStudio loop (feature 009)

A detector set turns versions into the four kinds of probe dataset miStudio needs, sends them, and
reads miStudio's evidence back. Every number on the screen is traceable to recorded IDs: no mapping
is guessed, no ID is pasted, and every figure names its set and sample.

## The four roles

| Role | Count | miStudio registers it as |
|---|---|---|
| Training rows | exactly one | `role: train` |
| In-distribution test | exactly one | `role: eval`, `distribution: in_distribution` |
| Out-of-distribution evaluation | one or more | `role: eval`, `distribution: out_of_distribution` |
| Calibration negatives | exactly one | `role: calibration` |

"Exactly one" is enforced by the database (a partial unique index), not only by the form. Each
role binds a completed version, one of its splits, the input and label columns, a label mapping
from every raw value to `positive`, `negative` or `excluded` (values compare as strings), and
optionally a pair column. A null label value is mapped by the key `"None"` (miStudio also reads
`"null"`); it is counted like any other value, so a mapping that forgets it is refused.

A training or in-distribution role may list **label source columns**: the columns its label was
computed from (Humicroedit's `human_label` is defined from `meanGrade`, and `grades` are the ratings
behind it). Each must exist in the version and be neither the input column nor the label column.
D-3 does not audit them and says so: they predict the label by definition, and the probe reads only
the input column. Columns a labeler step wrote beside its label are set aside the same way, with no
declaration needed.

Calibration negatives record their basis: kept because a labeler scored them negative (the labeler
identity and the rule), labelled negative by people (`human_labelled`: the label column, the values
that select the negatives, which must be exactly the values the mapping sends to negative, who
labelled them and the rule), or assumed negative (an unlabeled corpus). A set with a behaviour key
names the evidence behind assumed negatives: the `screen` (each rule set whose positive signals
screened the rows, pinned by name, version and sha256, and run as `rule_set@1` with
`use = screen` over the role's input column in the build of the role's version, followed by a
`screen_filter@1` that drops on every boolean output of that step, with no later step changing or
adding rows; else `negatives_basis_unproven`), or, for a
behaviour with no rule signal, the `second_opinion` sample (`sample_rows`, at least
`SECOND_OPINION_NO_RULE_NEGATIVES`, 1,000; `estimated_positive_rate`). A basis with neither is
refused `negatives_basis_incomplete`; D-8 names the evidence in its note. Giving a saved set a
behaviour key later checks its saved roles the same way.

The **monitored text** is the text the probe will watch once armed. It defaults to the first
out-of-distribution role and can be any version, split and column.

## Checks before sending

`POST /api/v1/detector-sets/{id}/checks` (MCP: `dataworks_check_detector_set`) returns D-1 to D-11,
each **green**, **note** or **refused** with a reason and a next step.

| Check | Refuses when | A note when |
|---|---|---|
| D-1 Roles complete | a role is missing, there is no out-of-distribution role, a version is not completed, or no monitored text is named | — |
| D-2 Label mappings valid | a value in the split is unmapped (a null label included), a role has no positive or no negative, calibration negatives map anything to positive | — |
| D-3 Shortcut audit | feature 004's audit warns on the training or in-distribution rows, or its control failed (declared label source columns are excluded and listed) | — |
| D-4 No leakage across roles | feature 004 finds a near-duplicate pair crossing two roles | — |
| D-5 Calibration length matches | never | the quantile overlap of the calibration negatives with the monitored text is below `DETECTOR_LENGTH_OVERLAP_MIN` |
| D-6 Held-out roles not expanded | never | an evaluation role holds synthetic rows. When every such role binds a value-directed held-out split whose disjointness proof passed, the note names the proof and cites "R-03.38 as amended, ENH-006 D10" instead (see [Versions](versions.md#value-directed-held-out-splits)) |
| D-7 Labelers have calibration records | a labeler bound to a role's version has no record, or its latest verdict is `invalid` or `insufficient` | the verdict is `fails` |
| D-8 Calibration negatives basis | never | the basis is "assumed negative" |
| D-9 Wording alone | never | the words alone separate the labels unusually well: with an out-of-distribution role to compare against, the training or in-distribution test role scores more than 0.02 above every one of them at the same size; without one, at 0.97 or more. Also a note (never green) when it could not be computed (fewer than 20 rows of either class) |
| D-10 Gate results and evidence | never | per role: the diversity and contamination reports on the role's **exact** version (a report on another version, even its parent, reads "not measured", naming where it was measured; a `falls` verdict or overlapping rows are noted); an evaluation role's class under 20 rows, or below the rows its configuration planned; an evaluation role mixing generated and source rows (real-text tests are their own role); for a set naming a behaviour, no second opinion ("not measured", and when its training build ran no rule set, that the labels rest on the plan alone) or a partial one; and the definition notes: a catalogue definition that moved since the set or a version was made ("built against v1, now v2"), an archived behaviour, a catalogue meaning that differs from the set's, or "not verified" without miStudio. Nothing rebuilds and nothing is refused |
| D-11 Kept evaluation disjoint from new training | a role kept from an earlier version (an evaluation or calibration negatives role bound to another version than the training role's) has a row that is also a training row (same row key: a kept row copied verbatim into new training), value-directed or not (`cross_version_not_disjoint`, naming the role, `shared_rows` and both versions; ENH-006 D42 for calibration negatives, D45 for evaluation roles); or the kept role binds a value-directed held-out split and one of its held-out values, or one of its groups, is in the new training role's rows (naming the value and both versions); also refused when either role's rows lack a directing or group column, since a value or group there could not be seen (`missing_columns`) | — |

**D-9 always shows its figure.** It fits a bag-of-words classifier (TF-IDF word 1- and 2-grams,
logistic regression, five folds grouped by the pair column when the role has one) on a seeded,
label-stratified sample of at most `DETECTOR_TEXT_SEPARABILITY_ROWS` rows, and reports the
cross-validated AUROC (area under the ROC curve). The figure depends on the number of rows, so it is
compared with a reference only at the same size. It is computed once per version and cached; the
first check of a new version takes a few seconds longer. It never blocks a send: a dataset whose
wording gives the label away passed every other check once, and a probe trained on it learns the
wording.

**The length tolerance was measured, not chosen** (T-44). Against Humicroedit's 6,600 headlines
(median 67 characters), 2,000 OpenHermes chats (median 1,228 characters) overlap **0.000**, and
ColBERT news headlines of the calibration set's kind (median 64) overlap **0.777**. The default
tolerance, 0.5, sits between them. Identical distributions read about 0.9, not 1.0: the figure is
the share of each set inside the other's 5th-95th percentile range.

The checks also return the label values each mapping must cover (counted by feature 008's
projection, the function that writes the published files), both length profiles in characters and
in words, and the finest false positive rate the calibration negatives afford (1 / n).
`?detail=summary` keeps every verdict, reason and next step and leaves out the profiles, label
values and long details; the MCP tool asks for the summary unless `detail` is `full`.

**Label source columns.** A role may declare up to 1,024 `label_source_columns`, the columns its
label was computed from. Every role shares the set's label, so a column ANY role declares
(calibration negatives and out-of-distribution roles included) is set aside from D-3's audit, and
the reason names each role that declared it. A long derivation is easier to declare after a
`drop_columns` step has removed its helper columns.

### Repairing leakage (D-4)

When D-4 refuses a set because rows cross roles, `POST /api/v1/detector-sets/{id}/leakage-repair/plan`
(`dataworks_plan_leakage_repair`) reads the same leakage report D-4 read and, for each crossing pair,
keeps one side whole: `ood_eval` over `id_test` over `calibration_negatives` over `train` (two roles
of one kind keep the earlier-bound one). For each role that gives rows up it returns how many and a
one-step recipe of `drop_leakage_pairs`, pinned to the report's pairs file by SHA-256. Save it, build
it from that role's version (an ordinary build, under the ordinary gate), bind the new version and
check again.

## Benchmarks a set names

A set may name the benchmark sources (imported Hub datasets at a pinned revision) its behaviour's
data must not contain: `benchmark_source_ids` on create and update (MCP too; `[]` clears). The
contamination catalogue (`GET /api/v1/curation/benchmarks`, `dataworks_list_benchmarks`) is their
union over every live set, each with the sets naming it; no benchmark is named in code. Run
`dataworks_run_contamination_check` against them on the role's version, and D-10 shows the result.

## The send

`POST /api/v1/detector-sets/{id}/send` (MCP: `dataworks_send_detector_set`) is one job on the
`publish` queue. Per role it walks three steps; roles sharing a version share its publish and its
download.

1. **Publish** each distinct version through feature 008's single entry point, private unless you
   chose public. Repositories default to `<namespace>/<dataset>-v<n>` (Settings, Environment:
   `PUBLISH_DEFAULT_NAMESPACE`). A version already published and verified to that repository is
   reused. The first request answers `409 builds_pending` while 008 writes the files; send again
   when they finish.
2. **Download** each (repository, split) into miStudio and poll until `ready`. A `409` from miStudio
   (it already holds that repository and split) is never reused silently: the step fails with
   miStudio's dataset ID and the next step, unless an earlier send recorded that exact commit.
   **No token is sent**: miStudio reads a private repository with its own stored token.

   **Pinned downloads (feature 011).** When miStudio pins downloads (its schema lists `revision` on
   `DatasetDownloadRequest`, miStudio feature 035), the step sends `revision` = the publish commit,
   and after `ready` it checks the commit miStudio recorded: another commit, or none, fails the step
   with `mistudio_commit_mismatch` and both values. A `409` is then resolved by revision: only
   miStudio's row at this commit is reused, and otherwise the step fails naming the commits miStudio
   holds ("revision not recorded" for a download made before miStudio pinned). Each download step
   records `commit_check`: **verified by miStudio**, **claimed (miStudio does not pin downloads)**
   when the capability was absent, or **not checked (sent before feature 011)**. A recorded value is
   never changed afterwards; capabilities are read at send start and on each resume.
3. **Register** each role as a probe dataset, then compare miStudio's positive, negative and
   excluded counts with the version's (excluded where miStudio reports it). A mismatch fails the
   role, naming the counts that disagree.

**Behaviour key and meaning.** A set may name the **behaviour** it trains a probe for (its
behaviour key, for example `pii`, shared with miStudio's behaviour catalogue), beside what the
positive class means. When miStudio serves both `behaviour` and `positive_meaning` on its probe
dataset registration (read from its published schema at each send), every registration carries
them, and the send says **Behaviour sent to miStudio**. When it serves neither, or only one, they
are left out, the send completes, and each register step says why. An empty value is never sent.
Both are bound in the send's snapshot, so an approved send cannot change them; a view registered
without them is registered again, not reused, once miStudio serves them.

**Recorded definition (feature 017).** A set also records the behaviour definition it is made
against (`behaviour_definition`: key, definition version or "not verified", positive meaning,
whether miStudio's catalogue verified it). Give it as the `record` of `dataworks_resolve_behaviour`;
without one, the set takes the definition its training version's build recorded, when that names
the set's key. A definition naming another key than the set's is refused
(`behaviour_definition_mismatch`). Nothing is rebuilt when the catalogue later moves on.

A failed or cancelled send resumes from its first incomplete step; a recorded step is never sent
again. When every role is registered the screen shows the run request in miStudio's
`ProbeRunCreate` shape to copy into miStudio; miDataworks does not start the run.

**Agents.** An agent's send waits for one `hub_push` approval covering the whole send. The approval
binds a digest of the set's snapshot, every publish digest and every registration body; if the set
changes before the operator approves, the approved send refuses with `approval_mismatch`.

### Waiting for publish files

A send first writes each version's publish files (008 builds). The REST route answers
`409 builds_pending` while they run; the MCP tool `dataworks_send_detector_set` waits for those
builds (up to `wait_for_builds_s`, 900 s by default) and sends again, so an agent's approval is filed
in one call. The approval is unchanged: the resent request goes through the same `hub_push` gate.

### Several sets under one approval

`POST /api/v1/detector-sets/send` (MCP: `dataworks_send_detector_sets`) with `set_ids` sends a
behaviour's main set and its sub-question sets (each binding the same versions with another label
column and the same behaviour key) together: an agent's request waits for ONE `hub_push` approval
listing every set, every version and its repository. Approved, one send per set starts under one
`send_group_id`, in order; a version two sets share is published once (the later sends read the
first one's publish), and miStudio registers one view per set and role with that set's label
column. More sets than `DETECTOR_MAX_SETS_PER_SEND` (16) is refused naming the setting. A public
send of a set binding a version built under `private_only` (see [Versions](versions.md)) is
refused up front (`private_only_public_refused`), before any approval is queued.

## Results

`POST /api/v1/detector-sets/{id}/results/refresh` (MCP: `dataworks_refresh_probe_results`) asks
miStudio for the runs trained on this set's training view (`train_dataset_id`, when miStudio serves
the filter; otherwise it scans miStudio's newest 200 runs and the snapshot says "scanned the newest
200 runs; older runs may be missing") and stores a snapshot with `run_discovery`, each role's commit
and `commit_check`, and:
per set the AUROC (area under the receiver operating characteristic curve) with its 95% confidence
interval and counts, firing rates at the shipped threshold with their counts, the rung in
miStudio's own words (never composed from the number), and miStudio's caveats verbatim. A probe or
run miStudio later deletes stays in the snapshot, marked "no longer in miStudio".

The **paired score** is shown only from per-row scores that reproduce miStudio's reported AUROC
(inside its interval for miLLM scores; within 5e-4 for miStudio's own arrays). Sources are tried in
order: miStudio's scores for the exact evaluation behind the figure (when miStudio serves them,
feature 011), then a miLLM probe-verdict run over the role. miStudio's rows are joined to this
send's published rows by position through the publish build file, and are refused (with the reason)
when miStudio scored another commit or dataset, the build file is gone, a position is out of range,
or a label disagrees. The result names its `source` and why each other source was `skipped`; with
no accepted source it reads "not available" with the reason; nothing is estimated.

A probe **marked as a training reward** (`POST /api/v1/reward-marks`) never appears as an
evaluation again. There is no unmark.

### Reward marks set by an export

A probe is marked **used as a training reward** by hand (`POST /api/v1/reward-marks`) or
automatically by an export: when any export (TRL, miForge set or reward bundle)
completes for a version whose build bound a `probe_verdict` label run, each such run's miStudio probe
(the run identity's `mistudio_probe_id`) is marked, naming the export. The version's rows carry that
probe's verdicts, so a model trained on them has the probe as its teacher, and the probe's later
scores are not an evaluation (FR-009.69 - FR-009.71). With `MISTUDIO_BASE_URL` unset nothing can be
marked (a mark names a miStudio); the export still completes and its job result lists the probes
under `reward_marks.unmarked_reason`. A mark is never removed.

## Minimal pairs

A minimal pair is a seed row and a counterpart that differs by a small edit flipping one concept.
Both rows share a `pair_id` (the seed's row key), so the version can serve as an evaluation set
with a pair column. A flip the judge does not verify is dropped and counted; it is never kept.

By operator decision (2026-10-07), the generator is a **chain of four stages**, each of which
records its own provenance. It is not one recipe operator. One operator could not do the work:
the generator and the judge are two endpoint roles, and the check is a judge label run, which an
operator cannot write.

| Stage | What runs | What it records |
|---|---|---|
| `generate` | A 007 generation run in mode `minimal_pairs`: one edit per seed row, written into the text column of a `detector` dataset | the seed row, the served model and revision, the steering requested and reported |
| `scope` | A build: `dw_generated_rows@1`, then `minimal_pair_scope@1` | keeps each counterpart within the edit cap, and its seed; drops the rest with a reason (`no_edit`, `edit_too_large`, `seed_without_counterpart`, `not_in_run`) |
| `judge` | A 005 judge label run over the `scope` version, with a rubric pinned by ID | a verdict for every seed and counterpart, and the judge's identity (`labeler.json`) |
| `pair` | A build: `minimal_pair_join@1`, binding the generation run and the judge run | keeps a pair only when the judge read the seed as `flip_from` and the counterpart as `flip_to` |

The `pair` stage writes these columns on both rows: `pair_id`, `pair_role` (`seed` or
`counterpart`), `pair_seed_row_key`, `pair_generator_model_id`, `pair_generator_revision`,
`pair_steering_state` (as miLLM reported it, `not reported` when it reported nothing),
`pair_steering_set_hash`, `pair_edit_chars`, `pair_edit_words`, `pair_judge_verdict`,
`pair_judge_run_id`, `pair_judge_model_id`, `pair_judge_revision`, `pair_judge_rubric` and
`pair_judge_identity_hash`.

**Refused before anything runs:**

- a seed split that is held out (`HELD_OUT_SEED`), or a version with no held-out split
  (`HELD_OUT_MISSING`);
- a judge that is the same model as the generator (`JUDGE_IS_GENERATOR`, 007's rule);
- no judge configured (`JUDGE_UNCONFIGURED`);
- a flip verdict the rubric cannot give (`FLIP_VERDICT_UNKNOWN`), or a pairwise rubric.

Judge independence is checked again in three more places: in 007's generation worker against the
served model, in 005's label-run preflight when the judge run starts, and in `minimal_pair_join@1`
against the identities the two runs recorded.

**Routes:**

- `POST /api/v1/minimal-pair-chains/plan` is a dry run;
- `POST /api/v1/minimal-pair-chains` returns `202` with the chain;
- `GET /api/v1/minimal-pair-chains[/{id}]` reads chains, with each stage's run, job and version
  ID;
- `POST /api/v1/minimal-pair-chains/{id}/resume` and `.../cancel` resume or cancel a chain.

The MCP tools are `dataworks_plan_minimal_pairs`, `dataworks_start_minimal_pairs`,
`dataworks_list_minimal_pair_chains`, `dataworks_get_minimal_pair_chain`,
`dataworks_resume_minimal_pair_chain` and `dataworks_cancel_minimal_pair_chain`.

A Beat task (`midataworks.detector_sets.advance_minimal_pair_chains`, every 30 s) starts each
stage when the previous one completes. A stage that fails, is cancelled, or refuses to start stops
the chain as `failed`, with `failed_stage` naming it and `error` saying why. **Resume** continues
from that stage:

- a run is resumed through its own resume;
- a build is requested again;
- a refused start is tried again.

With a single miLLM, the judge stage refuses with `MODEL_NOT_LOADED` until the operator loads the
judge model. Load it, then press Resume. miDataworks never loads a model. If an agent started the
chain and the judge run would exceed the agent row threshold, the stage stops with
`APPROVAL_NEEDED` and an operator resumes it.

The Detector sets screen lists the chains under **Minimal pairs**. Each chain shows its four
stages, the stage it stopped at, and the counts of verified and unverified pairs.

## The probe reproduction gate

Before a probe-verdict run labels anything, it scores one evaluation miStudio recorded and requires
miLLM's AUROC inside miStudio's interval. The target is chosen in this order: a results snapshot of
this set's own send; then a **reproduction link** whose rows were checked by content hash before a
counts-only one (rows proven identical are the stronger evidence, whichever role they hold); then
in-distribution before out-of-distribution; then the newer. The plan says why the target won and
lists the others with their check levels.

- **Chats stored as JSON text** are parsed first with `chat_json_parser`; the probe plan refuses to
  send JSON chat text as one user turn. A parsed column is sent as its messages, and against
  miStudio's decoded chats the render form decides the agreement (below).
- **The render form is read from miStudio, never assumed** (2026-10-08). A link stores the probe's
  `render_form` as miStudio recorded it. Probes trained since miStudio `af7cdad2` record
  `{"generation_prompt": true, "add_special_tokens": false}`, which is the form miLLM serves. A
  null or absent form means "not recorded": the probe was rendered without the generation prompt
  and is never read as served. The scoring form then reads one of three things:
  - **equal by render rule**: miStudio's form renders each row as miLLM will. No token ids are
    compared, so this never reads "verified by token ids";
  - **differs**, with the reason: the input kinds or the render form produce a different sequence.
    For example, a user-ended row rendered without the generation prompt, or decoded chats against
    a text column;
  - **not verified**: the form is not recorded and every row ends on an assistant turn.

  A link stored before this keeps its text. When the run environment stored with it records the
  served form, the link carries a note saying its "no generation prompt" is wrong.
- **Identical inputs** share one row key: the gate scores each once and keeps every copy with its
  own label. The run, the gate and the link all state rows and distinct inputs, and name any input
  labelled both ways.
- **A failed check is not offered again.** The plan reads `failed` with the run that failed; a run it
  stopped cannot be resumed. Change the link, target, window, input form or model revision, or
  start with `reproduction_retry_reason` (recorded on the run).
- The one-input **preflight** names its bar: a length band of the window, or the window's own bar.

## Detector operators from an agent

009's two recipe operators, `hard_negative_miner@1` (keeps rows a probe scores near its threshold,
gets wrong, or either, from a completed probe-verdict run) and `feature_filter@1` (keeps rows where
named SAE features reached a cutoff, from a completed feature-tag run), have no run route of their
own and no one-step MCP tool: `dataworks_run_detector_operator` was retired on 2026-10-16 (operator
decision D6). An agent runs either in two calls, the same path the Recipes screen uses:

1. `dataworks_save_recipe` with one step, for example
   `{"operator": "hard_negative_miner", "version": "1", "params": {"probe_label_run_id": "lr_…",
   "mode": "near", "band_below": 0.1, "band_above": 0.1}}` (check it first with
   `dataworks_validate_recipe`; the parameters are the operator's manifest schema, readable with
   `dataworks_get_operator`).
2. `dataworks_build_version` with that recipe's revision, the input version, and a binding to the
   run the step reads: `{"kind": "label_run", "id": "lr_…"}`. A step reads only runs its build
   binds; an unbound run is refused.

Probe verdicts and feature tags themselves are label runs: `dataworks_start_label_run` with
`role: "probe"` and a `probe_id` (probe verdicts), or `role: "features"` (feature tags).

## Configuration

Settings marked changeable can be changed under **Settings, Environment** while miDataworks runs;
the others are set by the deployment.

| Setting | Default |
|---|---|
| `MISTUDIO_BASE_URL` | none; required to send |
| `DETECTOR_SEND_POLL_SECONDS` | 10 |
| `DETECTOR_SEND_DOWNLOAD_TIMEOUT_MINUTES` | 60 |
| `DETECTOR_SEND_JANITOR_LIMIT_SECONDS` | 900 |
| `DETECTOR_LENGTH_OVERLAP_MIN` | 0.5 (measured) |
| `DETECTOR_TEXT_SEPARABILITY_ROWS` | 2000 (D-9) |
| `DETECTOR_CAPABILITY_TTL_SECONDS` | 600 |
| `MISTUDIO_TIMEOUT_SECONDS` | 30 |
| `PUBLISH_DEFAULT_NAMESPACE` (changeable under Settings, Environment) | none; the send form asks |

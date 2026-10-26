# Versions, recipes and provenance (feature 002)

Feature 002 makes every dataset a series of immutable **versions**, each the output of recorded
inputs, a **recipe** and a seed, with every dropped or changed row explained. It covers the
**Version detail** screen (with **Compare** and "Why did this row leave?"), the **Recipes** screen
and its editor, and the guided **New dataset** flow. It serves twenty-eight REST routes (twelve under
`/api/v1/versions`, ten under `/api/v1/recipes`, six under `/api/v1/recipe-drafts`) and nineteen MCP
(Model Context Protocol) tools in the `datasets` category: `dataworks_build_version`,
`dataworks_list_versions`, `dataworks_get_version`, `dataworks_get_build_manifest`,
`dataworks_get_version_rows`, `dataworks_explain_row`, `dataworks_get_drop_log`,
`dataworks_list_row_events`, `dataworks_get_lineage`, `dataworks_compare_versions`,
`dataworks_verify_rebuild`, `dataworks_delete_version`, and `dataworks_list_recipes`,
`dataworks_save_recipe`, `dataworks_get_recipe`, `dataworks_revise_recipe`,
`dataworks_clone_recipe`, `dataworks_validate_recipe`, `dataworks_archive_recipe`. Recipe export,
import, build-from-recipe and drafts have no MCP tool; an agent builds through
`dataworks_build_version`.

The captures are written in both colour modes: `frontend/e2e/versions.spec.ts` writes
`e2e/captures/{dark,light}-version-detail.png`, `-compare.png` and `-row-history.png`;
`frontend/e2e/recipes.spec.ts` writes `-recipes.png` and `-recipe-editor.png`; and
`frontend/e2e/guided-flow.spec.ts` writes `-guided-flow.png` and `-guided-import.png`.

## How a version is built

`POST /api/v1/versions` (or **Build version** on a recipe card, which builds the recipe's head
revision through the same code) resolves the whole request **before** any job exists, so a refusal
comes back as a reason, not a failed job:

1. The dataset, every input (a ready, pinned source, or an undeleted version) and the recipe revision
   are looked up. The recipe is validated again against the inputs' real columns.
2. Bindings (finished label or generation runs the build consumes) are resolved, so no step calls a
   model during a build.
3. Column roles are set from the dataset's target type; at least one content column is required,
   because the row key is computed from content columns.
4. A seed is drawn if you gave none, and recorded. The **request digest** (dataset, inputs, recipe
   hash, seed, bindings, row-key scheme, role overrides) decides what happens next: a completed
   version with that digest is returned with `200` and nothing is built; an identical build already
   running is returned with `202` and `existing_job: true`; otherwise one job is queued (`202`).

The worker re-hashes every source file against the SHA-256 recorded at import, runs step 0
(assemble), then each recipe step through feature 003. A step whose identity matches a completed
earlier execution is **reused**, and the drop log says so. Finalizing writes per-split Parquet files,
their file and logical digests, and the manifest, then moves the directory into place **before** the
row commits, so a committed version always has its files.

An agent's build that would label more rows than the P-07 window allows waits for the operator's
`agent_label_rows` approval.

**What the job says while it runs.** The job's message names the step and its state: "step 3 of 12
(checksum_validate): queued for a curation worker; not started yet", then the worker's "running, N
rows processed", "recording its output", "reused a completed run", or "waiting on build job_..., which
computes it" when another build is computing the same step (two builds over the same input share
their identical steps). When that build finishes - its assemble included - the waiting build goes on
by itself. Cancelling a build that is only waiting takes effect at once; and the janitor wakes, on
every sweep, any build still waiting on a step that has ended and any cancelled build that nothing
would otherwise advance.

### Plan a build first (dry run)

`POST /api/v1/versions/plan` (`dataworks_plan_build`) takes the build request and writes nothing. It
answers whether the recipe is valid for these inputs, which steps count against the agent labelling
window and why (only a step that calls a model through an endpoint role, or a probe-verdict or
feature-tagging labeler, counts; Data Designer expressions, filters and balancers do not), the rows
counted, the window's total now, whether an agent would wait for approval, and each cell balancer's
cells and cap. It also checks every Data Designer template against the columns its step will see
from these inputs, naming any rule that fails, so a template a build would refuse is caught here.

## Version detail

The header reads `<dataset> vN` with its state (**Head**, **Superseded by vN** or **Deleted**),
target type, rows, seed and who built it. Below it:

- **Stat tiles**: rows (with size and split count), rows dropped by the recipe, rows held out, and the
  content columns rows are keyed on.
- **Why rows left this version**: per step, the operator and version, rows in and out, and counts by
  event kind and reason code, with "reused from an earlier build" where it applies.
- **Lineage**: inputs (a version input opens that version), children, recipe hash, seed, row-key
  scheme, each split's logical and file digests, and whether each step was reused or computed.
- **Slots** from other features (audit, profile, diversity, publish state); feature 002's own
  **Identity** slot shows the request digest, the manifest SHA-256 and who built it.
- **Rows**: pages of 50 read from Parquet, filtered by split or "Contains" text.
- Actions: **Clone recipe** (names a new recipe built from this version's revision), **Compare**,
  **Verify rebuild**, **Delete version N** (asks for a reason, which is recorded) and **Export**
  (opens Publishing).

A finished version may carry warnings. `duplicate_metadata_disagrees` means copies of one row key
disagree on a metadata column; labels apply to every copy, so check they really are one row.

**Verify rebuild** rebuilds the version with reuse switched off, compares each split's logical
digest, records `match`, `mismatch` (with the first differing row key and split) or `failed`, and
discards the rebuilt files.

### Reading rows and whole splits

`GET /api/v1/versions/{id}/rows?columns=a,b` returns only the named columns plus `_dw_row_key`. For
a whole split, `POST /api/v1/versions/{id}/splits/{split}/download-url` (`dataworks_get_split_download_url`)
returns a signed URL that works for a set time (60 seconds to 24 hours) and streams the split as
Parquet or JSON Lines, optionally projected. The signature binds the version, split, columns, format
and expiry; a changed or expired link is refused, and a deleted version refuses even a live link.
Set `PUBLIC_API_URL` so the answer is a full URL; otherwise it is a path to prefix.

## Compare

**Compare with vN** compares with the parent (the first input version), or, when the version has no
parent, with the previous number of the same dataset; the API takes `?with=<version_id>` for any
other. The view shows rows per split and the difference, rows added, removed, changed and kept by row
key ("changed" is matched through the version's own `changed` events), the drop log side by side by
operator and reason, and distributions on **shared bins**: character length of each content column,
numeric metadata histograms, and top values of categorical metadata. Every caption names both sample
sizes. Counts are exact over whole splits, and a report is cached because both versions are
immutable.

## Why did this row leave?

Type a row key (at least 12 hex characters, a unique prefix is enough) or some text, or click
**History** on a row. The drawer answers **Present in this version** (with its split and any
changes), **Dropped** (the version, step, operator, reason and statistic where it left) or
**Not found** (naming how many versions of the lineage were searched). The walk follows `changed`
events backwards to the key the row entered with and forwards to where it left, through input
versions recursively, and shows the row's origin (a source and locator, or "generated from another
row"). A deleted version still answers from its events.

## Recipes

A recipe body is `dw.recipe/v1`: an ordered list of steps, each an operator **name and version**
with a parameters object. Its hash is the SHA-256 of the body's canonical JSON; the name,
description and step labels are outside it, so a label never changes the hash. Recipes are never
edited in place or deleted: **Edit recipe** saves a new revision, **Clone recipe** copies one
revision into a new recipe and records where it came from, and **Archive recipe** hides it (show it
with "Show archived recipes") while every version built from it stays readable.

Each card shows the head hash, step count, providers, revision count and versions built.

**The editor.** Add, remove and reorder steps with buttons (not dragging). Each step's **operator**
and **version** are chosen from lists read from the operator registry (`GET /api/v1/operators`):
choosing an operator picks its newest allowed version, an operator with no allowed version is shown
disabled with "(not allowed)", and a step pinning a version that is no longer installed keeps it,
marked "(not installed)". Each step's parameters are edited with the operator's **schema form**,
rendered from its manifest's parameter schema. **Edit as JSON** switches to a JSON box and **Edit
with the form** switches back. The JSON box is also used, with the reason shown under it, when the
operator's manifest cannot be read or the stored parameters are not a JSON object. Parameters that
are not valid JSON disable **Check recipe** and **Save**. **Check recipe**
(`POST /api/v1/recipes/validate`) states its answer under the steps ("The recipe is valid: N steps
checked…", or how many problems were found) and lists every failing step beside the step itself;
the answer is withdrawn as soon as a step changes. The backend validates on save either way.

**Limits and checks.** A recipe holds at most **64 steps** (every validation answer states its
`limits`; a longer body is refused as `too_many_steps`, rule `max_steps`). Chain recipes version to
version for more, or use the native identifier operators that replace long template chains.
`POST /api/v1/recipes/validate` takes the body, or `recipe_revision_id` to check a saved revision,
and optional `columns`: with them each step's column references (including columns an operator
names in its parameters) and each Data Designer template are checked against the columns that step
will see, and a failed template names its rule (`too_deep`, `too_many_nodes`,
`unknown_reference`, ...). A build plan does the same against the build's real inputs.

**Saving without echo, and reusing steps.** `POST /api/v1/recipes` and `.../revisions` take the
body inline or `recipe_revision_id` (start from a saved revision's body), and a step may be
`{"include": "<revision id>", "from": i, "to": j}`: that revision's steps i to j (all when omitted)
are put in its place before validation, so the stored body - and its hash - is exactly what runs.
`?view=summary` answers the ids, revision, recipe hash and a count of steps per operator instead of
echoing the whole recipe; the MCP tools ask for the summary unless `echo` is set.

**Build version** on a card asks for the dataset and what to build from, both chosen by name: ready
sources and completed versions (newest first) in one list, the chosen one's ID shown with a copy
button. Source and version pages show their IDs too.

**Export recipe** downloads the backend's canonical bytes unchanged (`<name>.recipe.json`, with the
hash in an `X-Recipe-Hash` header). **Import recipe** recomputes the hash and reports **Imported**,
**Already present** (same name and hash) or **Refused** with reasons. A file under an existing name
with a new hash becomes a new revision of that recipe; a file that was re-serialised is accepted with
the note `file_not_canonical`.

## The guided New dataset flow

Seven steps come from `GET /api/v1/datasets/meta`, never from a frontend list: import, goal, profile,
curate, label, assemble, export. Each step's panel is supplied by the feature that owns it; a step
with no panel is reported as a fault, and progress on the other steps is saved. Feature
002 owns **Goal**: it creates the dataset (lower case, digits and hyphens) or changes its target type
while no version exists, and shows which columns rows will be keyed on. Assemble links to
**Generation**.

Progress lives in a **draft** saved as you go (and when you leave the screen); a draft may be invalid
and has no hash.

**Curate** (feature 004's panel) lists suggested steps, each with an **Add** button (Remove exact
duplicates, Drop empty rows, Drop rows too short or too long, Remove near duplicates, Normalise
chats, Parse chats stored as JSON text, Remove benchmark overlap, **Sample rows**); **Add another
step** adds any registered operator. A suggestion this deployment does not offer is disabled and
says whether it is not installed or not allowed. The steps use the recipe editor's step rows
(operator and version lists, schema form, reorder, remove) and are checked against the registry as
they change; problems are marked beside each step.

**Save and build.** **Build version** saves the draft's steps as a recipe (named
`<dataset>-recipe` unless you rename it; a new revision only when the steps changed since the last
save) and builds the version from the draft's inputs, in one action. While it cannot run, the
reasons are listed under it, in the order to fix them: choose a source (Import), create the dataset
(Goal), add a step (Curate), fix the marked steps, name the recipe, or wait for the running build.
**Save draft as recipe** saves without building. When the build completes, its version is recorded
on the draft: Profile, Label, Assemble and Export read it, and Label chooses it for you. A reload
during a build follows the same job again.

**The step rail** marks a step done only when what it makes exists: a source (Import), the dataset
(Goal), a built version (Profile and Curate), a started label run (Label). A step you moved past
without finishing reads "not done" with the reason, for example "needs a built version".
**Profile** before the first build says it reads a built version and offers **Go to Curate to build
the version**. **Build a version from this source** on a source's page continues the journey on
Goal (no dataset yet, with a dataset name suggested from the source) or on Curate.

**Help.** Every step, and the Datasets, Recipes, Label runs and Operators screens, has a **Help**
link to the Help screen's topic for it. **Start over** discards the draft.

## Value-directed held-out splits

A held-out split normally holds only rows from sources (FR-002.31). The exception (BRD-03 R-03.38 as
amended, ENH-006 D10) is a split chosen **by value**: `split@1.1.0`'s directing columns (see
[Curation](curation.md#the-split-operator)) send every row whose genre, generator or other declared
value is held out to its own held-out split, and the build **proves** those values are absent from
training. Finalize recomputes the proof over the split files it wrote and stores it on the split:

- `directed_by`: the directing columns, the held-out values, the identity and group columns;
- `proof`: `passes`, the held-out values, the number of distinct training values per column,
  `intersection` (held-out values found in training, with their training row counts; must be empty),
  `null_rows` and `outside_rows` (rows of the split that are not held out; must be none), the groups
  shared with training (evidence only: D-4 is the refusal), rows, generated rows and the training
  splits read.

`GET /api/v1/versions/{id}` (`dataworks_get_version`) returns both on each such split. A failing
proof refuses the build (`held_out_not_disjoint`, naming the value and its count, or
`held_out_value_null`). A child version keeps the split's direction and proves it again over its own
files, so a child that brings a held-out value into training is refused. A published or exported
manifest carries the proof on the split (`extensions.held_out_proof`) and, when the split holds
generated rows, the caveat `generated_held_out` (severity note). Matching is exact (no case
folding), but a training value that differs from a held-out value only in case or whitespace
(`Fiction`, `fiction `) is recorded in the proof as `near_values` (column, held-out value, training
value, training rows; sorted, the first 20 listed and `near_values_count` the total), and the caveat
repeats them: they never refuse, they show where "absent from training" holds only by spelling
(ENH-006 D42). Anyone can recompute the proof from
the published files: `cd backend && python -m scripts.check_held_out_proof <manifest.json> <dir>`.

## Behaviour builds: definition, plan and census

A dataset built for a behaviour detector (feature 017) records what it was built against. No
behaviour is named in the code: every value below is the behaviour's own data.

- **The behaviour key.** Recipes (on the named recipe, never its body, so the recipe hash does not
  change), generation templates (not part of their hash), generation runs, label runs and review
  queues take an optional `behaviour_key`: the slug miStudio's behaviour catalogue uses. A clone
  keeps the source's key unless the request names one (`null` clears it). A label review queue
  without a key takes its run's. A generation run's key is recorded when it starts and stands: when
  a detector set or rubric related to the run now names another key, the run answer says so in
  `behaviour_note` and changes nothing.
- **The definition.** `GET /api/v1/behaviours/{key}/resolve` (`dataworks_resolve_behaviour`) reads
  the definition from miStudio's catalogue now (no cache): version, positive and negative meaning,
  target false-positive rate. It answers `verified` only when miStudio served that key; otherwise
  "not in the catalogue" (its catalogue answered `404 not_found`) or "not verified against a catalogue" (miStudio is
  not configured, did not answer, or refused). Without miStudio every step still works; you supply
  the definition. The answer's `record` is what a build records: pass it as `carrier_plan@1`'s
  `definition`.
- **The carrier plan.** `carrier_plan@1` runs over an index source of exactly `rows` rows and gives
  each row its genre, placement (`start`, `middle`, `end`, `none` or `whole`), framing, kind, form,
  length band (in characters), each sub-question's planned answer (`planned_<name>`) and, where the
  element goes, the slot `{{SLOT}}`, by exact seeded shares of the vocabulary you declare (JSON in
  the step's `vocabulary` and `shares`; an omitted dimension is uniform). A value the vocabulary
  does not declare is refused naming it and its field (`vocabulary_value_undeclared`); a field with
  more than `BEHAVIOUR_MAX_VOCABULARY_VALUES` (64) values is refused naming the setting, and so is
  a vocabulary making more than 100,000 genre x placement x framing x length band cells. A
  `templated_share` of rows is marked `generator = template` for the synthesiser route. The plan
  also writes an empty carrier text column, which a carrier generation run fills.
- **Recorded on the version.** Finalize stores `behaviour_definition`, `behaviour_configuration`
  (the parsed vocabulary, shares, sizes and parameters) and `behaviour_census` on the version, and
  `GET /api/v1/versions/{id}` returns them. A child version whose steps declare none inherits its
  parent's. A later step can name a parameter `"@input"` to run with what its input version
  recorded (resolved when the build runs, so another input is another step; a recipe check leaves it
  in place; an input that recorded nothing is refused `input_not_recorded`).
- **The declared-empty held-out split.** The plan declares a held-out split (default `test`) that
  receives no rows; finalize writes it as a zero-row file (`rows: 0`, `declared_empty: true`) and
  counts it as held out, so a carrier generation run's held-out guard passes without being relaxed.
  A child keeps it.
- **The census.** Rows per genre x placement x framing x length band against the plan, worst cells
  first, the templated share against `templated_ceiling`, and each framing's share and shortfall per
  genre. A cell or framing below plan, or a templated share over its ceiling, is **flagged, never
  refused**: the build proceeds and the version carries the warning `behaviour_census_flags`. A
  behaviour with no decoys reads "no decoys declared". A plan's census counts its plan rows; any
  later version counts only the rows that hold carrier text (`counted`), so a carrier run's
  candidate version, which keeps the plan rows beside the carriers, shows the cells generation left
  short.
- **Putting the element in (`insert_element@1`).** In `slot` mode it replaces each carrier's slot
  token (exactly once; a carrier holding it zero or two times is dropped `slot_missing` /
  `slot_repeated`, and a row with no text yet is dropped `no_carrier_text`); in `real_text` mode it
  inserts at the row's placement, the middle one at the nearest line break, else sentence mark
  (`.`, `!` or `?` only when a space follows, so a decimal point or a dot in a web address is no
  boundary), else space, else between two characters (never inside an accented letter, an emoji
  sequence or a flag). A real-text row with no source and no generator is refused
  `real_text_without_source`. Every row records `ins_start` / `ins_end` in Unicode code points (so
  `text[ins_start:ins_end]` is the element as inserted on any text), `element_inserted`,
  `boundary_kind`, `element_present` and `element_recovered`. A row whose `form` is a rendering the
  pinned rule set declares is inserted rendered and `element_recovered` says whether the rendering's
  normaliser gives the element back (`element_rendering` and `element_normaliser` name them); any
  other declared form is inserted as given. A row whose `kind` is one of the vocabulary's `decoys`
  takes a value from the rule set's decoy list of that id, drawn per row from the step seed. A
  real-text row stays `origin = source` and gains `behaviour_key`, `definition_version`,
  `generator = real_text:<source id>`, `carrier_id` and the plan values you give in
  `real_text_values`. An optional framing phrase from `phrase_list` goes just before the element,
  outside the span.
- **Labels from declared rules (`derive_labels@1`).** The label column, one `sq_<name>` per
  declared sub-question, `deployed_truth` and `label_conflict` come from truth tables you declare
  (JSON in `truth_tables`: inputs are plan columns, `planned_<name>` answers or boolean facts such
  as `element_present` or a rule set's output; `"*"` matches any value and the first matching row
  answers). A table that leaves any combination of its inputs' declared values unanswered is
  refused `truth_table_not_total`, at validation and when the build runs, and a row an earlier
  row shadows with a different answer (it would never be read) is refused
  `truth_table_row_shadowed`; every declared
  sub-question needs its table, and a vocabulary declaring more than `BEHAVIOUR_MAX_SUBQUESTIONS`
  (8) sub-questions is refused naming the setting. A veto never changes the label: `deployed_truth`
  is the label with the rule set's `<prefix>vetoed` applied (equal to the label, and the step report
  says "none declared", when no veto column is named). `label_conflict` is empty ("not measured"),
  never false, when the step has no rule signal; with the rule set's all-candidates `spans` output
  it reads `extra_candidate` (a positive signal fires outside the element) or `span_mismatch` (the
  element fires on its own but not where it was put), and with `target_checks` it reads
  `target_contradicted` (an instructed carrier whose rule signal disagrees with its planned
  answer). A decoy that a positive signal fired on is dropped `decoy_accidental_match`; a decoy whose
  `must_not_fire` column the input lacks is refused `decoy_check_missing` (run the rule set first,
  with the same `output_prefix`). Declare the
  columns the label reads in the detector-set role's `label_source_columns`, so D-3 sets them
  aside. Both operators may pin the same rule set as the `rule_set@1` step
  (`rule_set_name`, `rule_set_version`, `rule_set_sha256`); the build fills in its document. Over
  several version inputs, `"@input"` reads the record they agree on and refuses `input_ambiguous`
  when they recorded different ones.
- **Screening real text (`screen_filter@1`).** Run each screening rule set with `rule_set@1` and
  `use = screen` (its own output prefix), then `screen_filter@1` with `flag_columns`: every
  boolean output those steps wrote that marks a positive signal. A row any of them flags is dropped
  `screened_out`, a row one of them did not reach (an empty flag) `not_screened`: an unscreened row
  is never assumed clean. The drop log names the row, its source (`source=<id>`), the signal and
  the reason, never the row's text. A flag no step before the filter writes is refused
  (`missing_input_column` when the recipe is checked, `screen_column_missing` when it runs).
  Screened rows are the realistic calibration negatives of a behaviour's detector set (see
  [Detector sets](detector-sets.md)).
- **Counterfactuals made by code (`slot_siblings@1`).** After `insert_element@1` and before
  `rule_set@1` and `derive_labels@1`, it adds siblings of every slot carrier that holds its element
  (decoy kinds excluded; `kinds` narrows it): the same text with only the element span replaced,
  one sibling per declared variant in `variants` (JSON): `{"kind": "empty"}` (nothing),
  `{"kind": "decoy", "ref": "<decoy id>"}`, `{"kind": "vetoed", "ref": "<veto id>", "column":
  "<column holding the vetoed value>", "as_kind": "<declared kind>"}` or `{"kind": "rendering",
  "ref": "<rendering slug>"}`. Every character outside the span is unchanged; a decoy or vetoed
  value is rendered by the carrier's form, as the insertion would have put it. Siblings are new,
  generated rows, so they reach a held-out split only under a value-directed proof. Every row gets
  `pair_id` (its `carrier_id`) and `sibling_variant`: group a split by `pair_id` so a carrier and
  its siblings stay together, and give the detector-set roles `pair_column = pair_id`, so check
  D-4 refuses a set that separates them. Siblings are labelled by the declared rules like any
  row, which is why the step runs before them: an input already holding `deployed_truth` or the
  pinned rule set's outputs is refused `siblings_after_labels`. Edits code cannot make (another
  framing, an instructed carrier) go through the minimal-pair chain
  ([Detector sets](detector-sets.md#minimal-pairs)).

## A declared visibility floor

A recipe may declare `"visibility_floor": "private_only"` beside its steps (an operator's data
choice for a behaviour whose rows must not be public; no code names one). The version records it
(`visibility_floor` on the version), and so does every version built from it: a child of a
`private_only` version is `private_only` too, and so is a version built with a rule set derived
from one (its `rule_source`: the set's literals are words of those rows, and the set travels with
the version). Publishing check C-9 refuses a **public** push of such a version, naming the
declaration, and allows a private one (a "private" push into an existing public repository is a
public push, and is refused); a public detector-set send of it is refused before any approval is
asked. A recipe without the field keeps its hash.

## What is refused, and why

| Code | When |
|---|---|
| `recipe_invalid` (422) | a step names an operator or version that is not installed (`version_unavailable`, which names the current version and suggests "Clone recipe with current operators"; a newer version is never substituted), is not allowlisted (`operator_not_allowed`), has invalid parameters (`params_invalid`), or reads a column that does not exist at that point (`missing_input_column`); every failing step is listed |
| `recipe_name_taken` (409) | a new or cloned recipe reuses a name |
| `recipe_archived` (409) | revising, importing into, or building from an archived recipe; clone it instead |
| `recipe_labels_mismatch` (422) | step labels are given but not one per step |
| `recipe_import_invalid` (413/422) | the file is too large, not JSON, not a recipe file, or lacks a name or body |
| `hash_mismatch` (import refused) | the file's stated hash does not match its body: it was edited after export |
| `recipe_name_required` (422) | saving a draft as a new recipe with no name |
| `source_not_ready`, `source_unpinned` | an input source is not ready or has no pinned revision or content hash |
| `version_deleted` (409) | building from, comparing, verifying or reading rows of a deleted version |
| `no_content_columns` (422), `column_not_found` | no content column for the row key, or a role override names a column no input has |
| `binding_not_found`, `binding_incomplete` (409) | a bound run does not exist or has not finished |
| `source_hash_mismatch` | a source file changed on disk after import; import it again |
| `reserved_column`, `input_schema_conflict` | a source column uses the reserved `_dw_` prefix, or two inputs disagree on a column's type |
| `held_out_contains_generated` | a held-out split that is not value-directed would hold generated rows; move the generation step after the split, or direct the split by value |
| `held_out_not_disjoint`, `held_out_value_null` | a value-directed held-out split's proof failed: a held-out value is in training (named, with its count), or a row of the split is not held out or has no value; `details.next` names the tool to fix the recipe with |
| `no_parent` (409), `rowkey_scheme_mismatch` (409) | comparing with no parent and no other version chosen, or across two row-key schemes |
| `row_key_prefix_too_short`, `row_key_ambiguous`, `history_query_missing` | a row lookup with fewer than 12 hex characters, a prefix matching several keys, or neither key nor text |
| `version_in_use` (409) | deleting a version a running build or verify job reads; a version another feature still uses (a live label or generation run, a verified publish, a detector-set role, among others) is refused with that feature's own code and reason (409) |

**Delete is a tombstone.** It needs a reason; an agent's request waits for the operator's
`version_delete` approval. The rows are removed after the commit, but the manifest (still served by
`GET /api/v1/versions/{id}/manifest`), counts and events stay, and a version number is never reused.

## Archive (hide without deleting)

Test runs and abandoned work can be **archived**: hidden from the default lists and the pickers,
with every row kept. Datasets, label runs, generation runs and approvals can be archived.

- **Where.** **Archive** sits beside each dataset card on the Datasets screen, in a finished run's
  detail on the Label runs and Generation screens, and on a rejected or expired request on the
  **Approvals** screen, **Earlier agent requests**. Each list has **Show archived**; an archived item reads "Archived
  *when* (US Eastern) by *who*" and offers **Unarchive**, which puts it back exactly.
- **What is hidden.** The lists `GET /api/v1/datasets`, `/label-runs`, `/generation-runs` and
  `/approvals` leave archived items out unless `include_archived=true`. `GET /api/v1/versions`
  also leaves out the versions of an archived dataset, so they leave the version pickers; asking
  for one dataset (`dataset_id=`) lists its versions whether it is archived or not.
- **What still reads.** Everything: each item's own page, its versions, labels, records, manifests,
  lineage and publishes. Archive changes only the four `archived_*` fields, so a version, publish or
  kept dataset that points at an archived item is untouched.
- **Who and why.** The archive records who (the Settings name, or the calling product for an
  agent) and an optional reason. Every archive and unarchive is also appended to
  `dw_archive_events`, which is never updated or deleted. An agent may archive and unarchive
  without approval (`dataworks_archive_*`, `dataworks_unarchive_*`): nothing is deleted.

| Code | When |
|---|---|
| `dataset_busy` (409) | archiving a dataset while a job works on it: a build into it or reading one of its versions, a label or generation run over one of its versions, a publish, check, export or diversity job of one of them. Wait or cancel, then archive |
| `run_not_finished` (409) | archiving a label run that is awaiting approval, queued or running, or a generation run that is queued or running |
| `approval_not_archivable` (409) | archiving an approval that is pending, running, executed or failed; only rejected and expired requests can be archived |

**Archive or delete?** Delete removes a version's rows (a tombstone) and is refused while anything
still uses the version. Archive removes nothing, so it is the way to tidy test data that a publish
or a kept dataset still points at.

## Delete permanently (purge)

An **archived** dataset, label run, generation run or request can be deleted permanently: its rows,
everything that belongs only to it, and its files on disk. Only the operator can do it, from the
screen; agents can archive but never purge, and there is no MCP tool for it.

- **Where.** Turn on **Show archived** on the Datasets, Label runs or Generation screen, or in
  Settings, **Earlier agent requests**. Every archived item gets a checkbox; the bar above the list
  has **Select all** / **Deselect all** (shown partly ticked when some are selected), the number
  selected, and **Delete permanently**. An item that cannot be deleted says why under it and cannot
  be selected.
- **The confirmation** lists each selected item with what goes with it (versions, label runs,
  labels, recipes and so on, and the bytes on disk) or, if something changed since the list was
  read, why it will be skipped. Add a reason if you like; it is kept. Each item is deleted on its
  own, so one that is refused never stops the others.
- **What goes with a dataset.** Its versions (deleted ones too) and their files; their builds,
  steps, reports, profiles and diversity reports; every label run and generation run over one of
  its versions, with their labels, records and files; its guided drafts and warning levels; decided
  agent requests that name only it; the rule-set derivations made from its versions; and, when
  nothing else uses them, the job records, step
  outputs and recipes those used. A step output or recipe that another dataset's version also uses
  stays. A label run or generation run on its own takes its rows and files; a request is one row.
- **What stays.** One short record per purge: what was removed, by whom, why, and the counts and
  bytes. It is never changed or deleted.

| Code | When |
|---|---|
| `not_archived` (409) | the item is not archived; archive it first |
| `purge_blocked` (409) | something outside the purge still uses it. Each blocker is named: a publish record (publish records are permanent, so a published version keeps its dataset), a detector set, a miStudio registration, a calibration set, a review queue, an export, a version of another dataset built from it, a kept label run that derives from or reuses one of its runs, a saved rule set derived from one of its versions or a version that ran such rules (code `rule_source`: purge or replace the rule set first, so no rule set names a deleted derivation; D40), a pending request that names it, or a job still working on it |
| `agent_forbidden` (403) | the request came from an agent |

When two archived datasets are selected together and one is built from the other, the one built
from it goes first and the other follows in the same request.

A request that names a dataset and also its runs or requests deletes the runs and requests first,
each with its own record, then the dataset. If one of them could only go with the dataset (another
of the dataset's runs derives from it, say), the dataset's purge removes it, and the result lists
it as **deleted with** that dataset, counted among the deleted; the dataset's record counts it. An
item the request names that does not exist is reported as not found.

**Archive, delete or purge?** Archive hides and keeps everything. Delete removes one version's rows
and keeps its record. Purge removes an archived item and everything that belongs only to it, and
keeps only the purge record.

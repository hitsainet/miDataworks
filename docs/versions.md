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
| `held_out_contains_generated` | a held-out split would hold generated rows; move the generation step after the split |
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
  detail on the Label runs and Generation screens, and on a rejected or expired request in Settings,
  **Earlier agent requests**. Each list has **Show archived**; an archived item reads "Archived
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

# Curation and the shortcut audit (feature 004)

Feature 004 makes curation an explained trail and makes it impossible for a labeled version to hide
a shortcut. Every capability is an operator on feature 003's interface, so each step previews on a
sample, records why every dropped row left, and is reproducible from its seed.

## The shortcut audit

On 2026-10-05 a format column (joke or headline) predicted the humor label 88.5% of the time and
nothing said so. The audit now runs by itself after any build whose version has a label column, and
on demand with a chosen categorical column.

**The measure.** For every audited column the audit reports the **held-out balanced accuracy** of a
predictor that sees only that column: each value predicts the majority label of that value in the
training folds (an unseen value predicts the training majority; ties go to the lowest label code).
Five seeded stratified folds; scores are pooled out of fold. Balanced accuracy equals accuracy on a
balanced set and reads chance on an imbalanced one. An id column (one value per row) reads chance,
because held out, every value is unseen.

**The control.** The same figure is computed with the label permuted, five seeded times, and
averaged. It must read chance within a tolerance — 2 points (`CURATION_CONTROL_TOLERANCE_PP`), or
three standard errors of the control itself on small versions, whichever is larger — or the column
is marked **control failed**, both figures are shown and no warning is raised from it.

**Audited columns.** Every column with the `metadata` role, plus a derived length band (deciles of
content characters), the source (`_dw_source_id`) and the origin (`_dw_origin`), and `role` in a
cross-role audit. **Never scored**, and listed with the reason: content columns, other system
columns, the label itself, and every column a labeler wrote for the label (for example
`label_probability`), read from the labeler's manifest, never guessed from a name.

**The warning rule (P-19, confirmed S3-06).** A column warns when its figure is valid and at least
the margin above **both** chance and the permuted-label control, whichever is higher. The default
margin is **10 points**: 88.5% warns, 50.0% does not.

## Warning levels and who may change them

The margin is one global level with history, overridable per dataset with a reason (C4). The global
level is on the Settings screen; a dataset's override is on its Version detail audit card. Every
warning names the level in force and where it came from (the default, the global level, or the
dataset's override). Changing a level after an audit changes the warning, never the stored figures.

**Agents may read levels and never change them (P-09).** The three level writes —
`PUT` and `DELETE /api/v1/datasets/{id}/shortcut-level`, `PUT /api/v1/settings/shortcut-level` —
answer `403 agent_forbidden` to an agent before anything is written, and the table itself refuses
any row whose origin is not the operator.

**A warning refuses a public push and a detector-set send** (features 008 and 009) and allows a
private push, recorded as a caveat (P-01). An audit that could not be computed (too few rows per
class) is reported as such and is never a pass.

## The cell balancer

`cell_balancer` caps every (value × label) cell of one column at the smallest cell, so the column
then predicts nothing. Its preview shows the cells before and after, the cap, the rows kept, values
that are almost all one label (at least 99% of at least 50 rows), and a fresh audit of every column,
so a new shortcut created by balancing is visible before anything is built. "Build
`<column>`-balanced version" creates a new version whose parent is the audited one; the original is
never edited. A value of the balanced column is excluded with `exclude_values`; a value of another
column (Reddit `news` is a value of `source_label`, not of `format`) with a `metadata_value_filter`
step before the balancer. The balancer refuses an empty cell (naming it and the value to exclude),
the label column, a content column, a label-derived column and a one-class label.

## The label balancer (and why it is not the cell balancer)

`label_balancer` samples every class of the label down to the smallest class (or to
`max_per_class`, when smaller), **within each split separately**: no row moves between splits and no
row is ever duplicated. Which rows it keeps depends only on the rows and the step seed. Dropped rows
carry `label_cap` with the class, its size and the cap; the report re-audits every column on the kept
rows. It refuses an input without the label column, a row without a label, a split with one class,
and a class you listed in `classes` that has no rows in a split.

It is a separate operator on purpose. The cell balancer's promise is narrow and checkable: after it
runs, the named metadata column predicts nothing, and the label's relation to everything else is
otherwise untouched. It refuses the label column because balancing the label against itself is not a
shortcut fix — it is a different act: **selecting rows by their label.** That act has costs the cell
balancer never has, so it gets its own step that a reader of the recipe cannot miss:

- the class prior becomes artificial, so a model or probe trained or evaluated on the result is
  calibrated to the balanced prior, not the real one;
- after a split, the evaluation splits are resampled by the label, so their figures stop being
  figures of the real distribution;
- dropping rows by label can create a new shortcut (whatever correlates with the dropped rows),
  which is why the report re-audits.

So the warning is **mandatory**: there is no parameter that turns it off. Every run's report carries
it (`leakage_warning`, code `label_balanced`); the version's lineage lists the step, and so does the
lineage of every version built from it (marked inherited); Version detail shows it in the Lineage
panel; a publish shows the note `N-label_balanced` (it refuses nothing) and writes the step into the
manifest's `extensions.lineage.label_balancing` and the card's "Label balancing" section. The v1
contract's caveat codes are unchanged (a new code would be a contract revision across miStudio and
miForge).

Oversampling is not offered: copies of one row end up on both sides of a split, the leakage the
leakage check exists to find.

## The split operator

`split` assigns every row to a named split. Parameters are parallel lists (`split_names`,
`split_fractions`, `held_out`), plus `stratify_by`, an optional `group_column` and the mode
(`reassign` or `keep_source`). Within each stratum every non-first split takes `round(fraction × n)`
rows drawn with the step seed and the first split takes the rest — the prototype's numbers exactly
(10,914 rows → test 1,090, train 9,824). A group stays in one split (a whole group that would
overshoot a target stays for a later split, and the shortfall is reported); a generated row never
goes to a held-out split; a stratum smaller than the number of splits goes to the first split. The
split's roles reach the version, and the leakage check runs after the build.

## The sample operator

`sample` (selector, added 2026-10-19 after the novice walk could not label "25,000 rows of a
200,000-row source") keeps a seeded random sample of the rows of the whole input:

- **By count** (`mode: count`, the default; `count` rows) or **by share** (`mode: fraction`;
  `round(fraction × n)` rows, half up, at least one of a non-empty input). Exactly one size is given:
  the other one, or neither, is refused when the recipe is saved (the operator's `check_params`,
  run by the registry beside the schema check), not when the build reaches the step. A count above
  the input keeps it whole and the report says `kept_whole`.
- **Deterministic.** Rows are put in (row key, occurrence) order first, so file order never matters;
  the draw is `permutation(n)` from the `seed` parameter when given, else from the build's step seed,
  which the version records. The same input, parameters and seed keep the same rows.
- **Provenance.** Every row left out has a `dropped` event, reason `not_sampled`, with its rank in the
  draw (`sample_rank`) and the number kept (`{"value": k, "comparator": "kept_below"}`): a row is kept
  when its rank is below k. "Why did this row leave?" therefore names the draw for every missing
  row. The report lists rows in, kept and dropped, the seed and where it came from, and rows per
  split before and after. No row moves between splits.

In the guided flow it is the **Sample rows** suggestion on the Curate step.

## Leakage, contamination, profile and clusters

- **Leakage** finds exact duplicates, near duplicates (MinHash, recorded as `basis: lexical`) and
  rows sharing a group value across splits or roles. Feature 008's check C-3 and feature 009's D-4
  read the same result.
- **Contamination** measures word 13-gram overlap with a benchmark imported by feature 001 at a
  pinned revision; `decontaminate` drops rows over a threshold naming benchmark, revision and item,
  and refuses any other revision.
- **Profile** reports counts, nulls, length and turn histograms, duplicates and clusters, each with
  its scale and sample. A figure that cannot be computed says why and what would enable it; it is
  never zero.
- **Clusters** use a lexical basis in M1 (hashed TF-IDF, mini-batch k-means); the stored model lets
  feature 007 place another version's rows into the same clusters without refitting.

## The operator catalogue

| Operator | Kind | What it does |
|---|---|---|
| `profile` | report | The profile above |
| `shortcut_audit` | report | The audit above |
| `leakage_check` | report | Leakage across a step's splits |
| `contamination_check` | report | Overlap with a pinned benchmark |
| `cluster` | report | Lexical clusters |
| `normaliser` | mapper | One chat format; never strips newlines, role markers or code |
| `chat_json_parser` | mapper | A chat stored as JSON text parsed into messages in place; a value that is not a chat is dropped with the reason (`chat_json_unparseable`, `chat_json_not_a_chat`), never guessed |
| `dedup_exact` | deduplicator | Feature 002's row key; optional whitespace collapse (off) |
| `dedup_minhash` | deduplicator | Near duplicates; groups across splits kept and reported |
| `dedup_embedding` | deduplicator | Near duplicates by embedding cosine (default 0.95) from the `embeddings` endpoint role, on the `labeling` queue with the shared miLLM lease and `X-miLLM-Load-Policy: refuse`; blocked exact cosine up to `CURATION_EMBED_EXACT_MAX_ROWS` (refused above); rows longer than `CURATION_EMBED_MAX_INPUT_CHARS` are kept, not sent and not compared (never truncated); vectors cached by row key under `cache/embeddings/<spec_hash>/`; report `basis: embedding` with the specification (no key) |
| `length_band`, `turn_count_band`, `empty_content`, `ngram_repetition` | filter | Heuristic filters with threshold controls |
| `metadata_value_filter` | filter | Drops listed values of a column |
| `decontaminate` | filter | Drops benchmark overlap |
| `trl_validate` | filter | TRL column contract (filter mode); check mode serves feature 008's export |
| `cluster_embedding` | report | Clusters on the embedding basis (the `embeddings` role, `labeling` queue, shared lease, refuse-load); rows that cannot be embedded are counted as unclustered; `basis: embedding` with the specification (no key) |
| `cell_balancer`, `cluster_balancer` | selector | Caps cells or clusters |
| `label_balancer` | selector | Equal classes per split by seeded undersampling; always carries the leakage warning |
| `split` | selector | Split assignment |
| `sample` | selector | A seeded random sample by count or share; every row left out logged with its rank in the draw |

**From Data-Juicer** (feature 003's allowlisted catalogue): `dj_alphanumeric_filter`
(non-alphanumeric ratio), `dj_special_characters_filter` (special-character ratio) and
`dj_character_repetition_filter` (character repetition). Word n-gram repetition is native
(`ngram_repetition`). **Language identification is not offered**: no allowlisted operator provides
it and no approved library does, so the profile reports it as not computed.

**Embedding basis (2026-10-17).** With the `embeddings` endpoint role configured, `dedup_embedding`
and `cluster_embedding` work on embedding vectors, and the profile run from a version adds
"near duplicates (embedding)": groups of rows at cosine 0.95 or more, with the endpoint's model and
origin recorded (never its key). The figure is "not computed", with the reason, when the role is
not configured, when the profile has more than `CURATION_EMBED_EXACT_MAX_ROWS` rows (sample it),
or when the endpoint fails; it is never a zero. The endpoint is part of the profile's identity, so
configuring or changing it computes a new profile. A `profile` recipe step has no endpoint and
reports the figure as not computed. Empty texts are never sent to the endpoint (it refuses them);
they are skipped and counted.

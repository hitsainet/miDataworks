# Calibration and human review (feature 006)

Feature 006 checks a model labeler against human labels before its labels are trusted, and records
every human decision on a row without ever editing one. It is the **Calibration** screen, the
**Review** screen, and the gate target card on the **Approvals** screen (and in the approvals banner). It has twenty-two REST routes and
twenty MCP tools in two categories:
`calibration` (`dataworks_import_calibration_set`, `dataworks_build_calibration_set`,
`dataworks_list_calibration_sets`, `dataworks_get_calibration_set`, `dataworks_compute_calibration`,
`dataworks_list_calibration_records`, `dataworks_get_calibration`, `dataworks_get_gate_verdict`,
`dataworks_get_calibration_targets`, `dataworks_set_calibration_target`) and `review`
(`dataworks_preview_calibration_mapping`, `dataworks_list_review_queues`,
`dataworks_create_review_queue`, `dataworks_get_review_queue`, `dataworks_close_review_queue`,
`dataworks_get_review_rows`, `dataworks_review_decide`, `dataworks_get_review_decisions`,
`dataworks_draw_audit`, `dataworks_get_audit_status`). The two miForge routes (post candidates, read
back) are an application API with no MCP tool.

The captures are written in both colour modes by `frontend/e2e/calibration.spec.ts`
(`e2e/captures/{dark,light}-calibration-records.png` and `-calibration-add-set.png`) and by
`frontend/e2e/review.spec.ts` (`-review-item.png`, `-review-audit.png` and `-review-closed.png`).

## Calibration sets

A calibration set holds human labels for the rows of one version, for one question and its label
set (positive first). **Add a calibration set** offers two sources:

- **From an imported dataset.** Give a version, the question (the exact text the labeler is asked),
  the labels and a column mapping (`dw.calibration-mapping/v1`). The form maps a numeric
  human-label column with two cut points: *positive at or above* and *negative at or below*. Rows
  between the cut points are excluded. The API also accepts a value-to-label map. Optional
  extras are a per-rater column (a digit string such as `33110`, or a list of integers), a topic
  group column, stratum columns, and a reference-row selector (a column and a value).
- **From a review queue.** Pick a calibration-labeling queue. The latest **operator** decision on
  each item becomes its human label: an override's label, or the model's label for an accept. A
  flag is not a label. Agent decisions never become human labels.

The **mapping preview** runs about 400 ms after you stop typing and writes nothing. It shows rows,
positives, negatives, rows excluded by the cut points, reference rows and groups. If every row's
ratings are sorted, it warns that rating positions are ranks, not rater identities. The ceiling
then holds out a random rating per row and never a fixed position. Each set records its licence
class (taken from 008's licence table; a version with no recorded source is `private_only`), its
provenance, its deciders and how many decisions were made with model output visible.

## Computing a record

Choose a set, enter a label run ID and press **Compute a record**. This starts a
`calibration_compute` job (`202`, with a room the screen follows). The job reads labels the run
already wrote and never calls a model. It writes the record, its checks and its verdict in one
transaction at the end, so a cancelled or failed job writes nothing. Every seed is recorded, so
recomputing the same inputs gives the same `metrics_sha256`.

Each record card shows four tiles, and each tile names its sample size:

| Tile | What it is | Shown "Not available" when |
|---|---|---|
| AUROC vs human labels | Area under the receiver operating characteristic curve (AUROC) with its 95% confidence interval (CI), from 2,000 bootstrap resamples | one human class is missing |
| Held-out human rater | The ceiling: across 20 draws, one held-out rating per row is scored against the consensus of the rest; rows need at least 3 ratings | there is no per-rater column, the label rule is not numeric, or no draw's consensus has both classes |
| Same-group pair accuracy | How often the labeler scores the positive row of a group above its negative row, with a cluster bootstrap by group | there is no group column, or no group holds both classes |
| Rows it is confident on | The share of rows at or above the run's positive threshold | the run has no thresholds, or the score is discrete |

The card also shows a reliability chart (10 equal-width bins, greyed under 30 rows, available as a
table; probabilities only) and, with reference rows and groups, a **beats its reference** line.
When the same labeler also labeled other versions, a domain warning compares the length profile of
the set's version with theirs. It never changes a verdict.

### Checks that guard the gate

Each figure on the card that claims quality names the check that would show it wrong. A failed
check strikes through its tile and is listed as "does not measure what it claims".

| Check | Guards | Fails when |
|---|---|---|
| `both_classes` | AUROC, ceiling | 0 positives or 0 negatives after mapping; no usable ceiling draw |
| `label_permutation_null` | AUROC | over 200 shuffles of the human labels, the 95% AUROC range excludes 0.5 |
| `row_alignment` | AUROC, comparison | the scores, read a second time by row key, do not reproduce the reported value |
| `ceiling_circularity` | ceiling | the held-out rater scores 0.99 or more, or a draw of 20 or more rows held out the same position every time |
| `shortcut_comparison` | AUROC | a metadata column alone predicts the label as well as the labeler. Each metadata column of the set's version is scored by 004's held-out audit (FR-004.29: balanced accuracy of its per-value prediction, which for a two-label set is the AUROC of that 0/1 prediction) on the same labeled rows; the columns the human label is computed from (the mapping's label and ratings columns) are excluded as label sources, and a column whose permuted-label control is invalid is left out with its reason. The record's `metrics.shortcut` holds the figures. When 004 refuses (fewer than 5 rows in a class, one class), the check reads `not_applicable` with 004's reason; it never passes |
| `negative_class_control` | pairs, reference | the same metric with negative rows in the positive slot has a 95% interval above 0.5: "negative rows also win…; the metric measures something other than the concept" |

## Verdicts and gate rules

Each record gets one verdict, decided in this order. Every comparison is `>=`, so a value exactly
on the bar passes.

1. **`invalid`**: a check on the AUROC failed. A failed check on the ceiling or its comparison does
   not invalidate the verdict. It marks the ceiling invalid and unused, and the other rules decide.
2. **`insufficient`**: there is no AUROC, because one human class is missing.
3. **`passes`**, by the first rule that holds:
   - `operator_target`: an operator target is set and the AUROC CI lower bound reaches it;
   - `held_out_rater`: a valid ceiling exists and the labeler's comparison AUROC reaches the lower
     bound of the rater's interval;
   - `default_c3`: no target and no valid ceiling, and the AUROC CI lower bound reaches **0.70**.
4. **`fails`**, naming the rule it tried and both numbers.

The pill states this in words, for example *Passes · level with held-out rater* or
*Fails · CI lower bound 0.660 < 0.70 (default)*. `GET /api/v1/calibration-status` returns the latest
verdict for a labeler, or `none_recorded`. 005's Label step callout reads it, as do 008's check C-6
and 009's check D-7. C-6 treats `invalid`, `insufficient` and a missing record as amber, which
refuses a public push. A `fails` verdict adds a note.

### Gate targets

The editor above each question group shows the current target, or *default (C3)*. A target is an
AUROC CI lower bound above 0.5 and below 1. Targets are never edited: each change adds a row, the
latest one wins, and the history is kept. The operator sets a target directly. An agent's
`dataworks_set_calibration_target` waits for a **`gate_target_write`** approval (`202`). The
approval card shows the question, the old and new target, and each labeler whose latest verdict
would change (old → new, with its versions), or "No verdict would change". Stored verdicts are never
rewritten. The card shows the verdict each record would get when it is next decided.

## The Review screen

Queues are listed on the left with their kind, progress (*n of m decided*) and creator. The current
item is on the right. Keys: `a` accept, `o` override, `f` flag, `r` reject, `j` next row, `k`
previous row. Keys are ignored while you type in a field.

| Kind | Made by | What it holds |
|---|---|---|
| Label review | **Create a review queue** from a label run | a sample stratified by outcome (default 100, at most 5,000) or an explicit list of row keys; model output shown |
| Calibration labeling | **Create a review queue** from a version, question and labels | a sample (default 200), stratified by probability bin when a label run is given; **model output hidden** unless you tick "Show model output", and every decision records whether it was visible |
| Audit | **Draw an audit** | 50 to 100 rows of a version (default 100), stratified by effective label × probability band |
| External candidates | miForge, over the application API | prompt/completion candidates (at most 500 per batch), marked with an orange **miForge** chip; text shown as text, never HTML |

### Decisions

| Decision | Operator | Agent | Notes |
|---|---|---|---|
| Accept label | yes | yes | refused where model output is hidden: there is no model label to accept |
| Override label (*Assign label* in calibration labeling) | yes | no | carries a label from the label set |
| Flag row | yes | yes | |
| Reject candidate | external queues only | no | |

Override, flag and reject need a reason. Assigning a label in a calibration-labeling queue does not.
Decisions are **append-only**: each is a new row with who decided, their origin and why, and the
history is listed oldest first. The **effective label** (`dw.effective-label/v1`, which every export
uses) folds that history. An operator override sets the label. An operator accept restores the
model's label. Any flag leaves the row *flagged, unresolved* until the operator decides again, and
the export leaves such rows out. An agent accept changes nothing. Decisions are keyed by row key and
question, so they survive a rebuild that leaves the row unchanged.

### The audit before a public push

008's check C-5 needs a **completed** audit before a public push. A private push only gets a
caveat. An audit is complete when every sampled row has an operator decision for its question in
that version. Earlier operator decisions count at once, and agent decisions never count. On
completion the audit records accept, override and flag counts and the agreement share, then closes
its own queue. A new draw supersedes, and closes, an audit still in progress.

### Closing a queue

**Close this queue** (`POST /api/v1/review-queues/{id}/close`, optional reason) keeps every item
and decision. A closed queue takes no new decisions or candidates. It is hidden from the list unless
**Show closed queues** is ticked. Closing a closed queue is idempotent: the first close is kept. An
agent may close only queues it created. There is no reopen.

## What is refused, and why

| Code | When |
|---|---|
| `MAPPING_INVALID` (422) | a named column is missing; the human-label column is a content column (the labeler would see it); the label set repeats a label; a map value is not a label; ratings cannot be read; the mapping or the queue yields 0 positives or 0 negatives |
| `ROW_KEY_CONFLICT` (409) | two rows share a row key with different human labels |
| `PER_RATER_TOO_LARGE` (422) | more than 200,000 rows carry per-rater ratings (`CALIBRATION_MAX_PER_RATER_ROWS`) |
| `CALIBRATION_SET_EXISTS` (409) | a set already has this version, question and mapping |
| `QUEUE_KIND_INVALID` (422) | a set is built from a queue that is not calibration labeling, or candidates are posted to a non-external queue |
| `RUN_NOT_COMPLETED`, `RUN_VERSION_MISMATCH`, `QUESTION_MISMATCH`, `ROWS_NOT_SCORED` (409) | the run is not finished, labeled another version, asked a different question, or left set rows unscored (the count is given). All are refused before a job is queued |
| `APPROVAL_REQUIRED` (403), `APPROVAL_STALE` (409) | an agent writes a target without approval; the target changed after the agent asked |
| `AGENT_DECISION_NOT_ALLOWED` (403) | an agent overrides or rejects ("Flag it with a reason and the operator will decide") |
| `DECISION_INVALID` (422) | an unknown decision; reject outside an external queue; a missing reason; an override label outside the set; accept with model output hidden |
| `QUEUE_CLOSED` (409) | a decision or candidate for a closed queue |
| `QUEUE_NOT_YOURS` (403), `AUDIT_IN_PROGRESS` (409) | an agent closes a queue it did not create; an audit queue is closed while its audit runs |
| `AUDIT_SIZE_OUT_OF_RANGE`, `AUDIT_NEEDS_QUESTION` (422) | an audit outside 50–100 rows; no question and no label run to take one from |
| `ROW_KEY_UNKNOWN` (404) | a label-review queue lists a row key the version does not hold |
| `ORIGIN_APP_REQUIRED` (400), `ORIGIN_APP_MISMATCH` (403), `PAYLOAD_TOO_LARGE` (413) | miForge omits `X-Dataworks-Agent`, posts to another app's queue, or exceeds 500 candidates or 65,536 bytes per candidate (`REVIEW_CANDIDATE_MAX_BYTES`) |

The thresholds above (C3's 0.70, the audit bounds, the draw, permutation and bootstrap counts) are
operator decisions fixed in `backend/src/services/calibration/constants.py`. They are not settings,
and a test pins each one.

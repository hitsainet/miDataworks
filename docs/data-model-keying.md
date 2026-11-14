# Keying rules for row-level facts (C-002.6, FR-002.40)

Feature 002 owns the row identity every other feature keys its facts on. This note is the contract
features 005 and 006 index against. It lives beside the data model so a change to either side is
seen by both.

## Row identity

- A **row key** is `sha256("dw.rowkey/v1\n" + canonical_json(normalised content columns))`
  (FR-002.19). The only implementation is `backend/src/services/row_keys.py::compute_row_key`.
- Rows with the same key are allowed; each copy has an **occurrence** number, 0, 1, 2, … in output
  order. `(row key, occurrence)` names one physical row inside one version (FR-002.21). Events and
  feature 003's conservation check count `(row key, occurrence)` pairs, never bare keys
  (FR-002.47).
- A label run scores each **key** once (the label applies to every copy) and states the rows it
  covers beside the keys it scores (`row_coverage`). The probe reproduction gate and a reproduction
  link state the same two numbers and name any key whose copies carry both classes; those copies
  stay, each with its own label, and are never resolved to one (2026-10-08).
- A key is valid only within its scheme. Keys from two schemes are never compared (FR-002.22).

## Model labels (feature 005)

- Keyed by **(labeler fingerprint, row key)** (FR-005.28, FR-005.29). The fingerprint is the
  label specification hash: endpoint role, model ID and revision, template or rubric version, and
  question.
- **Not** keyed by version, by occurrence or by row position. A row whose content did not change
  keeps its key, so its label resolves in every descendant version with no endpoint call
  (FR-002.29). A label applies to every copy of a key.
- Index: unique `(fingerprint, row_key)`; lookups are `row_key = ANY(:keys) AND fingerprint = :f`.

## Human review decisions (feature 006)

- Keyed by **(row key, question)**, with the version the decision was made in recorded beside it
  (FR-002.40). The version is provenance, not part of the key.
- Index: `(row_key, question)`; the recorded version is a plain column.

## What 002 guarantees in return

- A version never changes after completion (FR-002.3), so a key read from a version always means the
  same content.
- Row text never enters PostgreSQL (FR-002.32); facts reference rows by key, and the text is read
  from the version's Parquet with DuckDB.
- A changed row gets a new key at the step that changed it, and the row history names that step
  (FR-002.26), so "this row has no label" always has an answer.

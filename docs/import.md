# Importing data (feature 001)

Every dataset in miDataworks starts as a **source**: rows imported once, pinned to exactly what was
read, and never changed afterwards. Versions (see [Versions and recipes](versions.md)) are built
from sources. There are two ways in: a Hugging Face dataset, or files you upload.

## Import from Hugging Face

On the **Datasets** screen, or on the **Import** step of **New dataset**:

1. Type the **Repository ID**, for example `CreativeLang/ColBERT_Humor_Detection`.
2. Press **Preview**. The preview reads the repository's licence, configurations, splits, size and
   a few sample rows before anything is downloaded. Pick a configuration or a split from the
   preview if the dataset has several.
3. Press **Import** (or **Import the previewed dataset** in the preview).

The fields:

| Field | What it does |
|---|---|
| Repository ID | The Hub dataset, `owner/name`. It is checked before any request is sent. |
| Split (optional) | One split. Empty takes every split. |
| Config (optional) | One dataset configuration. Empty takes the default configuration. |
| Revision | A branch, tag or commit. Empty pins the import to the commit it reads now. |
| Access token (optional) | Only for private or gated datasets. |

**Pinned to a commit.** An import records the full commit it read. A version built from it is
therefore reproducible: the upstream repository can change, and your data does not.

**One import per commit.** Importing the same repository, configuration, split and commit again
answers with the source you already have. Pressing Import twice while the first import is still
queued or running answers with that same job, so one click never makes two copies.

**A slice of a dataset.** Through the API and `dataworks_import_hf_dataset`, `max_rows` keeps only
the first rows of each split and `data_files` reads only the named data files of the repository
(paths inside it, globs allowed). Both are read at the pinned commit, so the same slice imports the
same rows every time. The slice is recorded on the source (`import_slice`) and in every manifest
that names the source (its `extensions`), and it is part of the "one import" rule above: two
different slices of one dataset are two sources, the same slice twice is one.

**Large imports ask first.** An import larger than the confirmation size (50 GB by default; the
form states the size in force) must be confirmed before it starts.

## Find and profile a dataset first

`POST /api/v1/sources/hf/search` (`dataworks_search_hf_datasets`) searches the Hub by words, with
optional licence ids, and returns each repository's licence, size category, downloads, gating and
tasks. It runs in a worker with the stored token, like the preview, and never returns it.

After an import, `POST /api/v1/sources/{id}/profile` (`dataworks_profile_source`) reads a whole split
with DuckDB, before any version exists: rows, nulls per column, the share of each value of the
columns you name (a label, a flag, a locale) and the length distribution of the text columns. A
split over `SOURCE_PROFILE_MAX_ROWS` (5,000,000 rows) is refused with the cap named; profile one
split, or build a sampled version and use the version profile.

## Tokens

Public datasets need no token. For a private or gated dataset, either store a Hugging Face token
once in **Settings** (it is kept encrypted), or type one into the import form for that import only.
A token typed into the form is never stored with the dataset: it is held encrypted for at most
fifteen minutes and deleted by the worker that reads it.

## Upload files

**Upload** takes Parquet, JSONL (JSON Lines) or CSV (comma-separated values) files as one source, each file a split (for example
`train.csv` as `train` and `test.csv` as `test`). CSV options cover the delimiter, the quote
character, whether the first line is a header, the encoding, and whether column types are inferred
or every column is read as text.

## Rows from nothing (an index source)

A generator such as `synth_identifier` needs rows but no data. `POST /api/v1/sources/index` with
`{"rows": 8000}` (MCP: `dataworks_create_index_source`) makes a source of one integer column
(`idx` by default, from `start`, default 0) without an upload, up to 10,000,000 rows. It imports
through the same job as an upload and is content-addressed: asking again returns the same source.
An integer column is not detected as text, so build from it with `column_roles` naming it as
content (`{"idx": "content"}`).

### Session data

Recorded user traffic is **session data**. A JSONL file exported from a miStudio spot check (its
disagreements, with session and request IDs) is recognised when every column of miStudio's
published row format is present and `messages` holds role/content turns; nothing is renamed or
dropped. You can also mark any source as session data by hand (**mark as session data** in the
sources list). Every version built from a session-data source, directly or through other
versions, carries an amber `session_data` caveat in its manifest and card, and the publish screen
warns before a public push. The warning does not block the push. A mark you set can be cleared
only while no version is built from the source.

## Licences

Each source carries the licence its repository declares. Publishing reads it: a licence that does
not permit redistribution keeps a version private, and a source with no usable licence can be
annotated with one (`permits`, `private_only` or `forbids`), with the reason recorded. See
[Publishing and export](publishing.md).

## Without miLLM or miStudio

Importing needs only the Hugging Face Hub (or your files). Nothing on this page depends on the
other mi suite applications.

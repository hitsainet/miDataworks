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

**Large imports ask first.** An import larger than the confirmation size (50 GB by default; the
form states the size in force) must be confirmed before it starts.

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

## Licences

Each source carries the licence its repository declares. Publishing reads it: a licence that does
not permit redistribution keeps a version private, and a source with no usable licence can be
annotated with one (`permits`, `private_only` or `forbids`), with the reason recorded. See
[Publishing and export](publishing.md).

## Without miLLM or miStudio

Importing needs only the Hugging Face Hub (or your files). Nothing on this page depends on the
other mi suite applications.

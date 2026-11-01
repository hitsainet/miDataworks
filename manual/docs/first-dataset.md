---
id: first-dataset
title: Your first dataset
---

**New dataset** walks you through seven steps. Your progress is saved as you go, so you can leave and
come back. A step shows a green tick only when what it makes exists: a source, a dataset, a built
version, a started label run.

The example below takes a public humor dataset to 25,000 labeled rows.

## 1. Import

Type the repository ID, for example `CreativeLang/ColBERT_Humor_Detection`, press **Preview** to see
its licence, splits, size and sample rows, then **Import**. The import is pinned to the commit it
read. Details: [Importing data](guide/import.md).

## 2. Goal

Name the dataset and say what will train on it: a detector (text and a label), a chat model (SFT),
preference pairs (DPO, KTO) and so on. The goal decides which columns identify a row; you can change
it until the first version is built.

## 3. Profile

The profile reads a built version: lengths, duplicates and clusters, each with its scale and sample
size. Until the first version is built there is nothing to profile; build one on the Curate step and
come back. Details: [Curation](guide/curation.md).

## 4. Curate and build

Choose the steps your recipe runs. For this example, press **Add** beside "Sample rows" and set
**Rows to keep** to 25000. The draw is seeded, so the same seed always keeps the same rows, and every
row left out is logged with its place in the draw. Name the recipe (a name is suggested), then press
**Build version** and wait for "Built". Details: [Versions and recipes](guide/versions.md).

## 5. Label

Choose the version (the one you just built is chosen for you), a labeler (a classifier, a judge, or
a probe in miLLM) and how many rows to label, then start the run. Its progress shows on the step.
When it finishes, the result is compared with any two-valued label column the data already has, so
you can see how far they agree before relying on the labels. Details: [Label runs](guide/labeling.md).

## 6. Assemble

Assemble shows the splits of the labeled version, the shortcut audit (does any column other than the
text predict the label?) and the leakage check between splits.

## 7. Publish or export

Publish the version to the Hugging Face Hub, or export files for training. Checks run before anything
leaves the machine. Details: [Publishing and export](guide/publishing.md).

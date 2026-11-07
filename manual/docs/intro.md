---
id: intro
title: miDataworks Manual
sidebar_label: Introduction
slug: /
---

miDataworks is the data plane of the mi suite. It imports Hugging Face datasets, curates and labels
them, checks labelers against human labels, builds detector sets for miStudio and training sets for
miForge, and publishes versions to the Hugging Face Hub.

It works on its own. miLLM (an inference server), miStudio (an interpretability workbench) and
miForge (a training application) add capabilities when they are configured, and every screen says
plainly when an action needs one of them. With none of them, miDataworks is a complete dataset tool
against any OpenAI-compatible or TEI (Text Embeddings Inference) endpoint.

## Where to start

- [Getting started](getting-started.md): run miDataworks on your own machine.
- [Your first dataset](first-dataset.md): from a Hugging Face dataset to labeled rows, step by step.
- The **Guide** pages explain each part of the application in depth, in the order a dataset moves
  through it: [import](guide/import.md), [versions and recipes](guide/versions.md),
  [curation](guide/curation.md), [label runs](guide/labeling.md),
  [calibration and review](guide/calibration.md), [generation](guide/generation.md),
  [publishing and export](guide/publishing.md) and [detector sets](guide/detector-sets.md).
- The **Reference** pages are for people extending miDataworks or driving it from an agent:
  [writing operators](reference/operators.md), [row keying rules](reference/data-model-keying.md)
  and the [MCP tools and REST contract](reference/mcp-contract.md).

## Words used throughout

| Word | Meaning |
|---|---|
| Source | Rows imported once from the Hub or from files, pinned to exactly what was read. |
| Version | An unchangeable copy of a dataset after a recipe's steps. Every removed row is logged with its reason. |
| Recipe | A saved, hashed list of steps (operators) with their settings. |
| Operator | One step a recipe can run: a filter, deduplicator, sampler, labeler or report. |
| Label run | One labeler applied to a version's rows; its labels are recorded per row. |
| MCP | Model Context Protocol: the interface through which an AI agent calls miDataworks's tools. |
| AUROC | Area under the receiver operating characteristic curve: how well a score separates two classes (1.0 is perfect, 0.5 is chance). |
| TRL | Transformer Reinforcement Learning, Hugging Face's training library; exports are written in its formats. |
| SFT, DPO, KTO, GRPO | Training methods TRL supports: supervised fine-tuning, direct preference optimization, Kahneman-Tversky optimization and group relative policy optimization. |

The in-app **Help** screen carries a short summary of each step and links to the matching page here.

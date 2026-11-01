# Publishing and export (feature 008)

A built version leaves miDataworks in one of two ways: **publish** pushes it to the Hugging Face
Hub, and **export** writes files for training. Both start from the version's detail page (**Export**
opens Publishing) or the **Export** step of New dataset.

## Publish to the Hub

Publishing takes three steps, each of which you can see before the next:

1. **Build the files.** One Parquet file per split, plus a manifest. Building again for the same
   version and label column reuses the files already built.
2. **Run the checks** (below). This is a preview: a publish never trusts it and runs every check
   again itself.
3. **Publish.** Choose the repository and its visibility. **Visibility is private unless you
   choose public.** After the push, the published files are read back and compared with the built
   ones; a published version can be re-verified later.

The dataset card is drafted from the version's provenance; you can edit its prose and republish
the card on its own.

An agent (through the MCP, Model Context Protocol, server) may ask to publish, but nothing reaches the Hub until the operator
approves that exact request; a changed request needs a new approval.

## The checks

| Check | What it asks |
|---|---|
| C-1 | Does every source licence permit redistribution? |
| C-2 | Can the stored Hugging Face token write to this namespace? |
| C-3 | Does any near-duplicate pair cross into the held-out split? |
| C-4 | Which models labelled or generated rows? (Said every time, in the card.) |
| C-5 | Is there a completed audit sample for this version? |
| C-6 | Does every labeler have a calibration record? |
| C-7 | Does the shortcut audit warn? |
| C-8 | Does every rule set the version carries hold public values only? (See [Rule signals](rule-signals.md).) |
| C-9 | Was the version built under a declared visibility floor (`private_only`)? A public push of it is refused; a private one is allowed. (See [Versions](versions.md).) |

Each check ends in one of four outcomes:

- **green**: it passes;
- **note**: shown and written into the card; it refuses nothing;
- **amber**: it refuses a **public** push, and permits a private one with a caveat;
- **refused**: the push cannot succeed. C-2 (a token that cannot write) refuses every push; C-9
  refuses a public push of a `private_only` version (`private_only_public_refused`).

A check whose owner has not run (for example no audit sample yet) is amber, never a pass. The
[calibration](calibration.md) and [curation](curation.md) pages say how to clear C-5, C-6 and C-7;
[Rule signals](rule-signals.md) says how to clear C-8.

A version built from **session data** (recorded user traffic; see [Importing data](import.md))
carries an amber `session_data` caveat in every manifest and card. It is not a check: before a
public push the screen shows a warning that does not block it, and you may still publish publicly.

## Export files for training

**Export** writes files to disk without touching the Hub, so it needs no approval:

| Target | What it writes |
|---|---|
| TRL | A training set in the shape TRL (Transformer Reinforcement Learning, Hugging Face's training library) expects: `sft`, `dpo`, `kto`, `grpo_prompt` or `prm`. |
| miForge set | A `prompt_set`, `corpus`, `preference_pairs`, `test_set` or `retention_set` for miForge. |
| Reward bundle | A reward function for TRL's GRPO (Group Relative Policy Optimization) trainer, from a GRPO prompt version, a rubric and a judge model: `reward.py`, the rubric, its configuration (the names of the environment variables holding the judge's endpoint and key, never their values) and a README. |

Training files are Parquet by default, or JSONL. Each export has a manifest with its digest, and the files
can be downloaded from the export's record. A miForge set can also be delivered to miForge from a
private Hub repository by publishing with the `miforge_set` target.

## The handoff contract

Every published or exported version carries a manifest in the `midataworks.dataset-version/v1`
format (the JSON Schema is `docs/schemas/midataworks-dataset-version-v1.json` in the repository).
miStudio and miForge read it to know exactly which rows, splits and labels they received.

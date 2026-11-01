# Rule signals (feature 016)

A rule signal decides a fact about a text the same way in every app of the suite: whether a pattern
matches, whether a value is a well-formed identifier, whether two things sit close together. The
rules are deterministic, call no model, and are pinned by a shared contract file of cases, so
miDataworks, miStudio and miLLM read a text alike.

Authoring is through the agent tools (MCP) and the REST routes; the **Rule signals** screen is
read-only: it lists rule sets and validators and lets you try a rule set on a text.

## Validators

A validator checks one candidate value: a format (`card_pan`, `ssn_us`, `aba_routing`, `iban`,
`nhs`, `aadhaar`, `masked`) or an algorithm (`luhn`, `mod97_10`, `verhoeff`, `weighted_mod`,
`length_charset`). Each has an exact version; a rule names `id` and `version` and is never given
"the latest". A validator answers `valid`, a `reason` from its declared list, a confidence `tier`
and the normalised value, and can scan a text for every candidate.

Every declared reason of every validator version has at least one case in
`docs/schemas/rule-signal-cases-v1.json`. The file is append-only once published: a case is never
changed; a changed behaviour is a new validator version with its own cases.

## Rule sets

A rule set (`midataworks.rule-set/v1`, schema `docs/schemas/midataworks-rule-set-v1.json`) has:

- **signals**: `pattern` (a regular expression in a linear-time dialect: no backreferences or
  lookaround), `proximity` (a phrase within a distance of an anchor, in characters or in UTF-8
  bytes, as declared), `validator` (candidates from a scan, from another signal's spans, or from the
  element), and `lexical_weights` (weighted terms against a cut);
- **expressions** over signals: `signal`, `all`, `any`, `not`, `within_n`;
- **outputs**, the columns a build writes: `fired`, `count`, `first`, `spans` or a validator `field`;
- **vetoes**, which remove the candidates they overlap (a masked value, for example);
- **cases**: inputs and the outputs they must give.

Spans are counted in characters (Unicode code points), half-open.

### Saving

`dataworks_save_rule_set` is the only write. It refuses, naming the item and the next tool to call,
a document that:

- does not match the schema, or holds a string that is not Unicode NFC;
- exceeds a cap (signals, cases, terms, document size; each a runtime setting, named in the refusal);
- has a pattern outside the dialect, an unknown validator version, or a code rule (not available
  yet);
- lacks a case where each output fires and one where it does not, or a case for each veto;
- fails any of its own cases;
- uses a name that already belongs to another behaviour key.

Saving an edit makes version n+1; versions never change. The identity is the SHA-256 of the
document's canonical JSON (RFC 8785), so key order does not change it. `dataworks_validate_rule_set`
runs every check without saving and lists every failing case with each signal's verdict;
`dataworks_preview_rule_set` runs a rule set on one text, which is never stored or logged.

### Using a rule set in a build

A recipe step `rule_set@1` pins `rule_set_name`, `rule_set_version` and `rule_set_sha256`, with a
`use` (`label`, `veto`, `screen`, `decoy_check` or `conflict_scan`) and the `text_column`. The build
loads exactly the pinned version, re-runs its cases, writes the declared columns (with a prefix, plus
`vetoed` when vetoes are declared) and records the use. A newer version changes nothing until you
save the recipe with the new pin; `dataworks_plan_recipe_upgrade` shows the move and the difference.

Archiving hides a rule set from lists. A version a build used is never deleted.

## Rule sets travel with the data

A version's build records every rule set it ran. Its publish, its card draft, its exports and its
reward bundle list them in the manifest (`extensions["midataworks.rule-sets/v1"]`: name, version,
sha256, behaviour key, use, path), and the publish and the exports carry each document's exact
stored bytes at `rule_sets/<name>@<version>.json`, compared with the Hub like a data file. A
consumer re-hashes each file against the listing and re-runs its cases. A version built before
rule sets were recorded reads "rule sets not recorded", never an empty list.

## Public values

A file that leaves miDataworks publicly carries only public values. A case whose input is a valid
identifier must cite a published test or example value (or, for a routing number, a published
reference value); every string in a public file is read by every identifier validator, and an
undocumented valid reading is refused. Constructed values are chosen invalid on purpose (for
example a Social Security number with area 9xx).

## Rules derived from data, and their limit

A rule set derived from a labelled version remembers its source. A version built with it inherits
the source's visibility, so a public publish is refused when the source may not be public. The
guarantee covers the honest path: if someone types derived words into a new rule set by hand,
without its provenance, miDataworks cannot know where they came from.

# Operators: writing one, adding a package, allowlisting

Feature 003 (Operator Framework and Engines). Every processing step is an **operator** behind one
interface; recipes name operators by `name@version`, never by library or import path.

## The interface

An operator is an object with a `manifest` (`backend/src/operators/manifest.py`,
`OperatorManifest`) and a `run(batch, params, ctx)` method returning an `OperatorResult`
(`backend/src/operators/protocol.py`).

- `batch` is a `pyarrow.Table`; every row carries `_dw_row_key` and `_dw_occurrence`.
- `params` were validated against `manifest.params_schema` by the API **and again in the worker**.
- `ctx` (`RunContext`) is the only handle: the seeded `rng`, `check_cancel()`, `progress()`,
  `endpoint` (for the manifest's endpoint role), `sample`, `input_reader` (dataset scope),
  `tmp_dir`, and the event helpers `drop`, `change`, `add`, `assign_split`. An operator never opens
  a database session or reads settings.
- Every drop, change and addition is an event with a reason code, a reason and (for drops and
  changes) the statistic that decided it. A drop without a reason refuses at construction.
- After every batch the framework checks the kind's effects (`effects.ALLOWED`) and conservation:
  every input `(row key, occurrence)` is kept, changed with an event or dropped with an event.
  A violation publishes nothing for the step.
- A manifest with `thresholds` must implement `compute_statistics(batch, params, ctx)`; the
  Operators screen then draws the threshold control for it with no extra UI code.

## The supported JSON Schema subset

`backend/src/operators/schema_subset.py` is the one definition (also served at
`GET /api/v1/operators/schema-subset`). Top level: `type: object`, `properties`, `required`,
`additionalProperties: false` (mandatory). Properties: `string`, `integer`, `number`, `boolean`
or `array` of a scalar or enum; `enum`, `const`, `default`, `title`, `description`; numeric
bounds and `multipleOf`; `minLength`, `maxLength`, `pattern`; `minItems`, `maxItems`,
`uniqueItems`; extensions `x-unit`, `x-hint`, `x-advanced`, `x-widget` (`slider`, `textarea`,
`select`). Anything else — `$ref`, `oneOf`, `anyOf`, `allOf`, `not`, `if`, nested objects,
`format`, `patternProperties` — makes the operator `invalid_manifest`, with the keyword named.

## Native operators

Product operators live in `backend/src/operators/native/` modules, added to
`registrations.PRODUCT_OPERATORS` (one line). The registry reads that one list.

### Identifier and pattern operators (DLP dictionaries)

Six native operators build and label identifier data with no model and no template chain. Each is
deterministic, so a rebuild reproduces it, and each preview shows the values it computed.

| Operator | Kind | Writes | What it does |
|---|---|---|---|
| `synth_identifier` | labeler | `value`, `scheme`, `perturbation`, `valid`, `detail` | One synthetic identifier per row from a `seed` and the row's identity. `valid_share` of the rows are valid, laid out in the `delimiters` styles you choose; the rest carry one named `perturbations` entry. |
| `checksum_validate` | labeler | `cv_valid`, `cv_reason`, `cv_tier`, `cv_layout`, `cv_normalised`, `cv_detail`, `cv_start`, `cv_end` | Checks a value with the SAME rules `synth_identifier` uses, and gives the documented confidence tier (none, low, medium) and the value's character span. |
| `phrase_proximity` | labeler | `pp_distance`, `pp_phrase`, `pp_near`, `pp_side` | The nearest listed phrase to an anchor span (or value), measured in UTF-8 bytes; `pp_near` is within `max_distance_bytes`. |
| `regex_match` | labeler | `rx_matched`, `rx_count`, `rx_first`, `rx_start`, `rx_end`, `rx_spans` | Matches a pattern and records every span. |
| `rule_label` | labeler | `output_column` (default `label`) | Writes a column from ordered rules over other columns. |
| `drop_columns` | mapper | (removes columns) | Drops named columns or glob patterns; refuses content and `_dw_` columns. |

**Schemes.** `card_pan`: issuer range and length (Visa 4: 13/16/19; Mastercard 51-55 and
2221-2720: 16; Amex 34/37: 15; Diners 36/38/300-305: 14; Discover 6011/644-649/65: 16-19; JCB
3528-3589: 16-19) and Luhn; `prefix_set: test` draws only from the BINs of the card networks'
published test numbers (a value is still a random number in that range). `ssn_us`: the Social
Security Administration's rules (area not 000, 666 or 900-999; group not 00; serial not 0000); there
is no check digit and no documented range of valid TEST numbers, so a valid generated number can
coincide with an issued one by chance (nothing links it to a person); invalid values use the
never-issued ranges, including the advertising range 987-65-4320 to 4329. `aba_routing`: Federal
Reserve prefix (00-12, 21-32, 61-72, 80) and the 3-7-1 check digit (not Luhn). `iban`: listed
country, its ISO 13616 length and BBAN letter/digit format (`check_bban`), MOD 97-10; `countries`
narrows the 45-country list. `nhs` (Mod 11) and `aadhaar` (Verhoeff) are implemented as the
extension points: a new scheme is one `Scheme` subclass and one line in `SCHEMES`.

**Perturbations.** `bad_check_digit`, `wrong_length`, `mixed_delimiters`, `adjacent_letters`,
`bad_prefix` (`bad_range` for `ssn_us`) and `masked`. A scheme refuses one it cannot make (an SSN
has no check digit). The `valid` flag is `checksum_validate`'s verdict on the value produced, never
an assumption: a draw whose verdict contradicts its intent is drawn again.

**Tiers.** Medium: a valid value in the scheme's popular layout with no letter (card, NHS, Aadhaar,
IBAN: no letter or digit) touching it. Low: the number checks out but its layout, a neighbouring
letter or (cards) an unknown issuer keeps it below medium. High is medium plus a phrase: add
`phrase_proximity` and a `rule_label` step:

```json
{"operator": "checksum_validate", "version": "1",
 "params": {"column": "value", "scheme": "card_pan", "before_column": "pre", "after_column": "post"}},
{"operator": "phrase_proximity", "version": "1",
 "params": {"column": "text", "phrases": ["credit card number", "Visa"], "max_distance_bytes": 100,
            "anchor_start_column": "cv_start", "anchor_end_column": "cv_end"}},
{"operator": "rule_label", "version": "1",
 "params": {"output_column": "tier", "rules": ["high if cv_tier == medium and pp_near == true"],
            "default": "@cv_tier"}},
{"operator": "rule_label", "version": "1",
 "params": {"rules": ["present if tier == high"], "default": "absent"}}
```

These four steps reproduce, byte for byte, the labels of a 38-step hand-written Data Designer
recipe on 8,000 rows, and pass all 62 examples printed in the consolidated DLP dictionary tables.

**Rules** (`rule_label`): `"<value> if <column> <op> <literal> [and ...]"`, first rule that holds
wins, else `default`; `@column` copies a column. Ops `== != < <= > >= in [a, b] not in [a, b]
is null is not null`; literals `true`, `false`, `null`, numbers, `'quoted'` text or bare words. A
value is compared with its own type and never converted: `"9"` does not equal `9`, and comparing
text with a number using `<` fails the step (`rule_type_mismatch`).

**Patterns** (`regex_match`) stay inside an RE2-like subset. Refused, with the rule named:
backreferences, lookahead and lookbehind, conditionals, atomic groups, possessive quantifiers, a
repeat count over 1000, and a variable quantifier inside an unbounded repeat (`(a+)+`), which can
backtrack catastrophically.

## Third-party packages (the `midataworks.operators` entry-point group)

1. Add the package, pinned exactly, to `backend/requirements-operators.txt` in a commit. Nothing is
   installed at runtime; CI builds the image.
2. The package declares, in its metadata:

   ```toml
   [project.entry-points."midataworks.operators"]
   my_ops = "my_package:operators"   # a callable returning operator instances
   ```

   Its manifests use `provider = "plugin:<distribution name>"` and a `curation` or `labeling`
   queue (`datajuicer` is reserved).
3. The entry point appears on the Operators screen as **Not allowed**. It is listed from metadata
   only and **not imported**: importing a module runs its code.
4. Allow it on the Operators screen with a reason. The change records your Settings name; agents
   can read the allowlist but are refused (403) if they try to change it. A new distribution
   version is a new decision. Revoking stops new runs at once (the API and the worker both check);
   it never alters existing versions.

## Engines

- **Data-Juicer** runs only in `hitsai/midataworks-datajuicer` on the `datajuicer` and
  `datajuicer_preview` queues. The offered operators are listed in `datajuicer/allowlist.yaml`;
  `datajuicer/scripts/build_catalogue.py` writes the committed catalogue, and
  `datajuicer/tests` (CI job `datajuicer-tests`) pins it, the subset copy and every operator's
  behaviour on a contract fixture. Upgrade by bumping the pin, rebuilding the catalogue and
  re-recording expectations (`datajuicer/scripts/record_expectations.py`), reviewing the diff.
- **Data Designer** runs only in `hitsai/midataworks-designer` on the `designer` queue (PADR ADR-010
  amendment, 2026-10-07): data-designer 0.9.4 needs `huggingface-hub<2` and `pyarrow<25`, which the
  backend's pins forbid. The backend never imports the library; it sends the step by task name and
  a finaliser (`midataworks.operators.step.finalize_designer`, on `labeling`) turns the worker's
  output into events. The endpoint URL travels in the task; the model key is sealed with
  `DESIGNER_HANDOFF_KEY` into Redis for one step and taken once by the worker. Every model call goes
  through the loopback relay (`backend/src/operators/data_designer/relay.py`).
  `designer/scripts/build_catalogue.py` writes the committed catalogue; `designer/tests` (CI job
  `designer-tests`) pins it and runs each offered column through the real library and the relay
  against a fake OpenAI-compatible server. A preview may also take a ready source
(`{"source_id": ..., "column_roles": ...}`): a seeded sample is given the build's system columns, so
no base version is needed. Two columns are offered (`dd_llm_text`,
  `dd_expression`); the judge, structured and validation columns have required fields outside the
  supported subset and are listed as rejected with that reason.

### Data Designer templates: what they see and what they may use

A Data Designer step is seeded with the version's **content** columns plus any other non-system
column its template names (a metadata column, or an earlier `dd_*` step's output, which is always
metadata). So chained `dd_*` steps work in one build. `_dw_*` columns are never visible. Nothing
changes role in the middle of a build: a content column is part of the row key, so making a step's
output content would change every row's identity mid-build (decision of 2026-10-21; the review
record has the reasoning).

Templates follow Data Designer's sandbox: no `{% set %}`, `{% include %}`, `{% import %}`,
`{% macro %}`, `{% extends %}` or `{% block %}`; no loop inside a loop and no recursive loop; no
`{{ self }}`; at most 600 template nodes and nesting depth 10; only Data Designer's filters.
`dataworks_validate_operator_params` (pass `columns` to check references) and
`dataworks_validate_recipe` ask the designer worker (`midataworks.designer.validate`), which applies
Data Designer's own checks and names the rule that fails (`template_invalid`); if the worker does not
answer, the answer is `designer_unavailable`, never "valid". A failed preview is stored and read
back as `status: failed` with Data Designer's message. A row an expression renders empty for is
dropped as `dd.empty_render` (an intended skip); `dd.render_error`, `dd.type_cast_error` and
`dd.not_generated` are failures. `dd_expression` is deterministic.

**Types are kept between steps.** A column stored as text reaches the next template as text: `"9"`
stays `"9"`, `"[ ]"` is not an empty list and JSON text is not an object. (Data Designer itself
parses every string of a record as JSON before rendering; miDataworks turns that off for records, so
one column can never hold mixed types.) An expression's output is stored as its `dtype` (`str`
text, `int` 64-bit integer, `float`, `bool`), and a value of another type fails the step with
`dtype_violation`. A step's preview lists the values it computed for each example. Recipe
validation with `columns`, and every build plan, check each template against the columns that step
will really see (the inputs plus earlier steps' outputs).

### Open item for feature 007 (FR-007.34)

Data Designer 0.9.4 sends `extra_body` **statically**: a `{{ _dw_row_key }}` template arrives at the
relay literally (spike 1.4, 2026-10-07). So a Data Designer request cannot carry its row key, and
the relay's per-request records (including the raw `X-miLLM-Steering` header) cannot be joined to
rows by key. 003 joins output to rows by the carried key columns and attributes failures in request
order (one request per row). Per-row steering state for 007 therefore needs either per-row
correlation from upstream Data Designer, or 007's own generator calling the relay with the
`X-Dataworks-Row-Key` header (the shape of the test operator `fx_relay_echo`). Also: a
generation-stage spec (FR-003.25) cannot run a Data Designer operator in-process any more; 007
needs a designer-queue dispatch for that path.

## Hand-off to feature 010 (MCP tools)

Tool names carry the `dataworks_` prefix (T-53):

| Tool | Route |
|---|---|
| `dataworks_list_operators` | `GET /api/v1/operators` |
| `dataworks_get_operator` | `GET /api/v1/operators/{name}/{version}` |
| `dataworks_list_operator_versions` | `GET /api/v1/operators/{name}` |
| `dataworks_validate_operator_params` | `POST /api/v1/operators/{name}/{version}/validate` |
| `dataworks_preview_operator` | `POST /api/v1/operators/{name}/{version}/preview` |
| `dataworks_get_operator_preview` | `GET /api/v1/operators/previews/{preview_id}` |
| `dataworks_preview_threshold` | `POST /api/v1/operators/{name}/{version}/statistics` |
| `dataworks_get_allowlist` | `GET /api/v1/operators/allowlist` |
| `dataworks_plan_recipe_upgrade` | `POST /api/v1/operators/upgrade-plan` |

Exemptions (no tool): `POST /api/v1/operators/allowlist` and `POST /api/v1/operators/allowlist/revoke`
(reason **P-09**: agents may not change the allowlist; the route refuses agent origin with 403), and
`GET /api/v1/operators/schema-subset` (a UI rendering contract, not an agent action).

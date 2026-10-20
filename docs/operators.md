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
  against a fake OpenAI-compatible server. Two columns are offered (`dd_llm_text`,
  `dd_expression`); the judge, structured and validation columns have required fields outside the
  supported subset and are listed as rejected with that reason.

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

<!-- GENERATED FILE: DO NOT EDIT BY HAND.
Regenerate: cd backend && python -c "from src.mcp_server.contract import write_contract; write_contract()"
backend/tests/contract/test_mcp_contract_generated.py fails when this differs from the registry. -->

# miDataworks MCP contract

Every tool the miDataworks MCP server (feature 010) registers, its category, the backend route it
calls and, when an agent calls it, the approval it waits for. Derived from the live registry by
`backend/src/mcp_server/contract.py`.

Agents: call `dataworks_howto` first; it carries the workflow and the result shapes.

Categories are switched by `MCP_TOOL_CATEGORIES`; all 13 are on by default. A category whose
owning feature has not served its routes yet registers no tools (ADR-027); the route ledger in
`backend/tests/support/mcp_ledger.py` lists each pending tool with its feature.

**220 tools in 13 categories.**

## `core` (9)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_health` | `GET /api/health` |  | The backend's health: overall status and each dependency (PostgreSQL, Redis, the data volume, miLLM, miStudio) with a reason when it is down. |
| `dataworks_howto` | none (served by the MCP server) |  | Guidance for agents: the workflow, the three result shapes (resource, pending approval, unavailable), the approval actions, jobs and secrets. |
| `dataworks_list_jobs` | `GET /api/v1/jobs` |  | List jobs (imports, builds, publishes, exports) with state and progress, newest first. |
| `dataworks_job_status` | `GET /api/v1/jobs/{job_id}` |  | One job's state, progress, result reference and error. |
| `dataworks_cancel_job` | `POST /api/v1/jobs/{job_id}/cancel` |  | Request a cooperative stop of a running or queued job. |
| `dataworks_list_approvals` | `GET /api/v1/approvals` |  | List approval requests: action, who asked, status, expiry, and the result once run. |
| `dataworks_get_approval_status` | `GET /api/v1/approvals/{approval_id}` |  | One approval: pending, executing, executed (with result_kind and result_id), failed (with error), rejected (with reason) or expired. |
| `dataworks_archive_approval` | `POST /api/v1/approvals/{approval_id}/archive` |  | Archive a rejected or expired approval: hide it from the default list. |
| `dataworks_unarchive_approval` | `POST /api/v1/approvals/{approval_id}/unarchive` |  | Unarchive an approval: list it again. |

## `datasets` (45)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_search_hf_datasets` | `POST /api/v1/sources/hf/search` |  | Search the Hugging Face Hub for datasets: repository, licence, size category, downloads, gating and tasks for each, most downloaded first. |
| `dataworks_profile_source` | `POST /api/v1/sources/{source_id}/profile` |  | Whole-split statistics of an imported source before building a version: rows, nulls per column, value shares of the columns named, text length distribution. |
| `dataworks_preview_hf_dataset` | `POST /api/v1/sources/hf/preview` | `secret_write` | Preview an HF dataset before importing it: configurations, splits, columns, sample rows, licence and size. |
| `dataworks_import_hf_dataset` | `POST /api/v1/sources/hf` | `secret_write` | Import an HF dataset as a source. |
| `dataworks_upload_file` | `GET /api/v1/sources/meta`<br>`POST /api/v1/sources/uploads` |  | Upload one Parquet, JSONL or CSV file (at most the operator's upload limit, and 10 MiB over MCP) as a source. |
| `dataworks_create_index_source` | `POST /api/v1/sources/index` |  | Make a source of N rows from nothing - one integer column, start..start+rows-1 - to drive a generator such as synth_identifier; no file upload. |
| `dataworks_list_sources` | `GET /api/v1/sources` |  | List imported sources with state, licence and size. |
| `dataworks_get_source` | `GET /api/v1/sources/{source_id}` |  | One source: origin, pinned revision, splits, columns, licence, annotations. |
| `dataworks_get_source_rows` | `GET /api/v1/sources/{source_id}/rows` |  | Read a page of a source's rows. |
| `dataworks_annotate_source` | `POST /api/v1/sources/{source_id}/annotations` | `source_annotate` | Annotate a source's licence, terms or detection result. |
| `dataworks_delete_source` | `DELETE /api/v1/sources/{source_id}` |  | Delete a source and its files. |
| `dataworks_get_source_meta` | `GET /api/v1/sources/meta` |  | Source limits and options: upload cap, large-import threshold, accepted formats. |
| `dataworks_get_dataset_meta` | `GET /api/v1/datasets/meta` |  | Dataset options: target types and their descriptions. |
| `dataworks_list_datasets` | `GET /api/v1/datasets` |  | List datasets with their latest version. |
| `dataworks_create_dataset` | `POST /api/v1/datasets` |  | Create an empty dataset. |
| `dataworks_get_dataset` | `GET /api/v1/datasets/{dataset_id}` |  | One dataset with its versions. |
| `dataworks_update_dataset` | `PATCH /api/v1/datasets/{dataset_id}` |  | Change a dataset's description or target type. |
| `dataworks_archive_dataset` | `POST /api/v1/datasets/{dataset_id}/archive` |  | Archive a dataset: hide it and its versions from default lists and pickers. |
| `dataworks_unarchive_dataset` | `POST /api/v1/datasets/{dataset_id}/unarchive` |  | Unarchive a dataset: list it again. |
| `dataworks_build_version` | `POST /api/v1/versions` | `agent_label_rows` | Build a new immutable version from a recipe revision and inputs. |
| `dataworks_plan_build` | `POST /api/v1/versions/plan` |  | Dry run of dataworks_build_version; writes nothing. |
| `dataworks_list_versions` | `GET /api/v1/versions` |  | List versions. |
| `dataworks_get_version` | `GET /api/v1/versions/{version_id}` |  | One version: recipe hash, inputs, splits, row counts, state. |
| `dataworks_get_build_manifest` | `GET /api/v1/versions/{version_id}/manifest` |  | The version's internal build manifest (steps, operator versions, counts). |
| `dataworks_get_version_rows` | `GET /api/v1/versions/{version_id}/rows` |  | Read a page of a version's rows, optionally only the named columns. |
| `dataworks_get_split_download_url` | `POST /api/v1/versions/{version_id}/splits/{split}/download-url` |  | A signed, expiring URL that downloads one whole split (optionally only some columns) with a plain HTTP GET, no token needed. |
| `dataworks_explain_row` | `GET /api/v1/versions/{version_id}/rows/history` |  | A row's history through the build: which steps kept, changed or dropped it, and why. |
| `dataworks_get_drop_log` | `GET /api/v1/versions/{version_id}/drop-log` |  | Rows each step dropped, counted by reason. |
| `dataworks_list_row_events` | `GET /api/v1/versions/{version_id}/events` |  | List the version's row events (drops and changes) with filters. |
| `dataworks_get_lineage` | `GET /api/v1/versions/{version_id}/lineage` |  | The version's lineage: inputs back to sources, with recipe revisions. |
| `dataworks_compare_versions` | `GET /api/v1/versions/{version_id}/compare` |  | Compare two versions: rows added, removed and changed, and distribution shifts. |
| `dataworks_verify_rebuild` | `POST /api/v1/versions/{version_id}/verify-rebuild` |  | Rebuild the version from its recipe and inputs and check the row keys match. |
| `dataworks_delete_version` | `DELETE /api/v1/versions/{version_id}` | `version_delete` | Delete a version (it becomes a tombstone). |
| `dataworks_list_recipes` | `GET /api/v1/recipes` |  | List recipes. |
| `dataworks_save_recipe` | `POST /api/v1/recipes` |  | Create a recipe with its first revision. |
| `dataworks_resolve_behaviour` | `GET /api/v1/behaviours/{behaviour_key}/resolve` |  | Read a behaviour's definition from miStudio's catalogue now (017): verified with its version, positive and negative meaning and target FPR; or "not in the catalogue"; or "not verified against a catalogue" when miStudio is absent or did not answer (then supply the definition yourself). |
| `dataworks_get_behaviour_status` | `GET /api/v1/behaviours/{behaviour_key}/status` |  | Where a behaviour's dataset stands (017): its objects found by their behaviour_key (rule sets, templates, recipes, runs, versions, label runs, review queues, sets, sends) with what each last said; the ordered journey, each step with the tool to call; the next missing step; and every approval the remaining steps may need. |
| `dataworks_get_clone_difference` | `POST /api/v1/differences` |  | What a clone changed (017): every difference from a to b over the behaviour key and the body ({op, path, old, new}; JSON Pointers), so a clone made with substitutions reads back exactly those fields. |
| `dataworks_get_result_page` | `GET /api/v1/results/{kind}/{id}` |  | One page of a large result table, worst first (017): a version's carrier census (kind census), a version's held-out proof values (proof), or a label run's agreement by stratum (agreement; shaped by column, positive_value and by). |
| `dataworks_get_result_download_url` | `POST /api/v1/results/{kind}/{id}/download-url` |  | A signed, expiring link to a whole result table as JSON Lines (017): the rows every page of dataworks_get_result_page would give, in the same order. |
| `dataworks_get_recipe` | `GET /api/v1/recipes/{recipe_id}` |  | One recipe with its revisions and the canonical body of each. |
| `dataworks_revise_recipe` | `POST /api/v1/recipes/{recipe_id}/revisions` |  | Add a revision to a recipe. |
| `dataworks_clone_recipe` | `POST /api/v1/recipes/{recipe_id}/clone` |  | Copy a recipe (one revision) into a new recipe. |
| `dataworks_validate_recipe` | `POST /api/v1/recipes/validate` |  | Check a recipe without saving: operators exist and are allowed, parameters are valid, the step order is legal and it has at most 64 steps (limits states every limit). |
| `dataworks_archive_recipe` | `POST /api/v1/recipes/{recipe_id}/archive` |  | Archive a recipe: hidden from lists, still readable, its versions unchanged. |

## `operators` (9)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_list_operators` | `GET /api/v1/operators` |  | List operators (the steps a recipe can use) with provider, kind and state. |
| `dataworks_list_operator_versions` | `GET /api/v1/operators/{name}` |  | The versions of one operator, one page with the total. |
| `dataworks_get_operator` | `GET /api/v1/operators/{name}/{version}` |  | One operator version's manifest, including its parameter schema. |
| `dataworks_validate_operator_params` | `POST /api/v1/operators/{name}/{version}/validate` |  | Check parameters against the operator's schema without running it. |
| `dataworks_preview_operator` | `POST /api/v1/operators/{name}/{version}/preview` |  | Run an operator on a sample of a version, a step's output or a source, and show kept, changed and dropped rows. |
| `dataworks_get_operator_preview` | `GET /api/v1/operators/previews/{preview_id}` |  | A preview's state and result: running, done with the result, or failed with the worker's own error (a Data Designer or Data-Juicer refusal is stored, not lost). |
| `dataworks_preview_threshold` | `POST /api/v1/operators/{name}/{version}/statistics` |  | The distribution of an operator's score on a sample, to choose a threshold. |
| `dataworks_get_allowlist` | `GET /api/v1/operators/allowlist` |  | The operator allowlist (installed plug-in operators allowed to run). |
| `dataworks_plan_recipe_upgrade` | `POST /api/v1/operators/upgrade-plan` |  | Which steps of a recipe have newer operator versions, and what changes. |

## `curation` (14)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_run_profile` | `POST /api/v1/versions/{version_id}/profile` |  | Profile a version: counts, nulls, lengths, duplicates, clusters; figures that cannot be computed say why. |
| `dataworks_get_profile` | `GET /api/v1/versions/{version_id}/profile` |  | The newest stored profile of a version (404 profile_not_run when none). |
| `dataworks_run_shortcut_audit` | `POST /api/v1/versions/{version_id}/shortcut-audit` |  | Run the shortcut audit: does any metadata column predict the label on held-out folds, above chance and a permuted-label control? Omit label_column to use the labeler's. |
| `dataworks_get_shortcut_audit` | `GET /api/v1/versions/{version_id}/shortcut-audit` |  | The stored audit with its warnings evaluated against the level in force now. |
| `dataworks_get_shortcut_cells` | `GET /api/v1/versions/{version_id}/shortcut-audit/cells` |  | Seeded sample rows of one (value x label) cell of the audit. |
| `dataworks_run_leakage_check` | `POST /api/v1/versions/{version_id}/leakage` |  | Find exact, near (MinHash) and group leakage across the version's splits. |
| `dataworks_get_leakage_report` | `GET /api/v1/versions/{version_id}/leakage` |  | The newest stored leakage report: crossing pairs counted per split pair. |
| `dataworks_get_leakage_pairs` | `GET /api/v1/versions/{version_id}/leakage/pairs` |  | One page of the leakage report's crossing pairs, with excerpts. |
| `dataworks_run_contamination_check` | `POST /api/v1/versions/{version_id}/contamination` |  | Measure word n-gram overlap with benchmarks imported at pinned revisions. |
| `dataworks_get_contamination` | `GET /api/v1/versions/{version_id}/contamination` |  | The newest stored contamination report. |
| `dataworks_validate_trl` | `POST /api/v1/versions/{version_id}/trl-validation` |  | Check a version against the TRL trainer's column contract; rules that could not be evaluated are listed as not checked, never as passed. |
| `dataworks_get_warning_levels` | `GET /api/v1/settings/shortcut-level` |  | The global shortcut warning level and its history (read-only for agents, P-09). |
| `dataworks_get_dataset_warning_level` | `GET /api/v1/datasets/{dataset_id}/shortcut-level` |  | A dataset's effective warning level, its source and its override history. |
| `dataworks_list_benchmarks` | `GET /api/v1/curation/benchmarks` |  | The benchmark catalogue (T-14): pinned revisions and whether each is imported. |

## `settings` (7)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_get_settings` | `GET /api/v1/settings` |  | Every setting an agent may read. |
| `dataworks_set_hf_token` | `PUT /api/v1/settings/hf_token` | `secret_write` | Store the Hugging Face token used for gated imports and publishing. |
| `dataworks_delete_setting` | `DELETE /api/v1/settings/{key}` | `secret_write` | Clear a stored setting (operator decision, 2026-10-07). |
| `dataworks_set_endpoint_key` | `PUT /api/v1/endpoint-roles/{role}` | `secret_write` | Set a role's API key. |
| `dataworks_get_environment` | `GET /api/v1/settings/environment` |  | Every environment setting with the value in force, the environment's value, where the value comes from (env or override), when a change takes effect, and whether an agent may change it; plus the last 50 changes. |
| `dataworks_set_environment` | `PUT /api/v1/settings/{key}` | `config_write` | Change a runtime environment setting (group runtime, agent_writable true). |
| `dataworks_reset_environment` | `DELETE /api/v1/settings/{key}` | `config_write` | Return a runtime environment setting to its environment value (removes the override). |

## `labeling` (28)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_list_endpoint_roles` | `GET /api/v1/endpoint-roles` |  | The four endpoint roles (classifier, judge, generation, embeddings) with protocol, URL and model. |
| `dataworks_fetch_models` | `GET /api/v1/endpoint-roles/{role}/models` |  | List the models the role's server serves (OpenAI /v1/models, or TEI /info). |
| `dataworks_list_templates` | `GET /api/v1/decision-templates` |  | List decision templates (the prompt and answer tokens a classifier run uses). |
| `dataworks_save_template` | `POST /api/v1/decision-templates` |  | Create a decision template. |
| `dataworks_get_template` | `GET /api/v1/decision-templates/{template_id}` |  | One decision template and its body. |
| `dataworks_clone_template` | `POST /api/v1/decision-templates/{template_id}/clone` |  | Copy a decision template as a new version, optionally with a changed body. |
| `dataworks_list_rubrics` | `GET /api/v1/rubrics` |  | List rubrics (what a judge run scores against), optionally only one behaviour's. |
| `dataworks_save_rubric` | `POST /api/v1/rubrics` |  | Create a rubric. |
| `dataworks_set_rubric_behaviour` | `PATCH /api/v1/rubrics/{rubric_id}` |  | Set or clear the behaviour key of a rubric no label run has used (frozen once used: clone it instead). |
| `dataworks_get_rubric` | `GET /api/v1/rubrics/{rubric_id}` |  | One rubric and its body. |
| `dataworks_clone_rubric` | `POST /api/v1/rubrics/{rubric_id}/clone` |  | Copy a rubric as a new version, optionally with a changed body or behaviour key. |
| `dataworks_test_endpoint_role` | `POST /api/v1/endpoint-roles/{role}/test` |  | Send one test request to the role's endpoint and report what it can do (logprobs, allowed tokens, structured output). |
| `dataworks_label_sample` | `POST /api/v1/labeling/sample` |  | Label a few rows now and show each verdict, to check a template or rubric before a run. |
| `dataworks_preview_band` | `POST /api/v1/labeling/keep-share` |  | Estimate the share of rows each threshold band would keep, on a sample. |
| `dataworks_get_keep_share` | `GET /api/v1/labeling/keep-share/{job_id}` |  | A keep-share preview's result: kept share per band, on its sample. |
| `dataworks_plan_label_run` | `GET /api/v1/label-runs/plan` |  | What a label run would do before starting it: rows to score, rows reused from cache, the agent rows already in the 24-hour window, whether it would wait for approval, and (classifier and judge runs) its cost: paid or not, price per call, estimate, the per-run cap, the ceiling and what paid runs have spent. |
| `dataworks_start_label_run` | `POST /api/v1/label-runs` | `agent_label_rows` | Start a label run on a version. |
| `dataworks_list_millm_probes` | `GET /api/v1/labeling/probes` |  | The probes imported in miLLM that a probe-verdict run can score with: what each was fitted on, its bar and evidence rung, and whether it fits miLLM's loaded model. |
| `dataworks_list_label_runs` | `GET /api/v1/label-runs` |  | List label runs with state, counts and who started them. |
| `dataworks_archive_label_run` | `POST /api/v1/label-runs/{run_id}/archive` |  | Archive a finished label run: hide it from default lists and pickers. |
| `dataworks_unarchive_label_run` | `POST /api/v1/label-runs/{run_id}/unarchive` |  | Unarchive a label run: list it again. |
| `dataworks_get_label_run` | `GET /api/v1/label-runs/{run_id}` |  | One label run: configuration, progress, counts per outcome, error. |
| `dataworks_get_label_run_labels` | `GET /api/v1/label-runs/{run_id}/labels` |  | A page of a run's labels: row key, verdict, probability and outcome. |
| `dataworks_get_label_run_agreement` | `GET /api/v1/label-runs/{run_id}/agreement` |  | Compare a label run with a label column its data already has (the run's target_column when none is named): confusion counts, agreement, each side's positive share, AUROC from the run's score, and a plain note. |
| `dataworks_review_disagreements` | `POST /api/v1/label-runs/handoffs/{handoff_id}/review` |  | Open label-review queues over exactly the rows where the hand-off's runs disagree with their compared column: at most 5,000 rows a queue, every stratum in the first, refused over REVIEW_MAX_QUEUES_PER_HANDOFF. |
| `dataworks_resume_label_run` | `POST /api/v1/label-runs/{run_id}/resume` | `paid_classifier_run` | Resume a stopped or failed label run from its last chunk. |
| `dataworks_rederive_label_run` | `POST /api/v1/label-runs/{run_id}/rederive` |  | Re-derive a classifier run's labels from its stored probabilities with new thresholds. |
| `dataworks_aggregate_judges` | `POST /api/v1/label-runs/aggregate` |  | Combine several judge runs on one version into one verdict per row. |

## `calibration` (10)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_import_calibration_set` | `POST /api/v1/calibration-sets/import` |  | Create a calibration set from an imported version's human-label column (FR-006.2 b). |
| `dataworks_build_calibration_set` | `POST /api/v1/calibration-sets/from-review` |  | Create a calibration set from a calibration-labeling queue. |
| `dataworks_list_calibration_sets` | `GET /api/v1/calibration-sets` |  | Calibration sets with their counts, licence class and provenance. |
| `dataworks_get_calibration_set` | `GET /api/v1/calibration-sets/{set_id}` |  | One calibration set. |
| `dataworks_compute_calibration` | `POST /api/v1/calibration-records` |  | Start a calibration job (AUROC with its 95% CI, the held-out rater ceiling, pairs, reliability, checks and the gate verdict). |
| `dataworks_list_calibration_records` | `GET /api/v1/calibration-records` |  | Calibration records, newest first, each with checks and verdict. |
| `dataworks_get_calibration` | `GET /api/v1/calibration-records/{record_id}` |  | One calibration record: metrics with sample sizes, every check, the verdict and its numbers. |
| `dataworks_get_gate_verdict` | `GET /api/v1/calibration-status` |  | The latest calibration verdict for a labeler (passes, fails, invalid, insufficient), or none_recorded. |
| `dataworks_get_calibration_targets` | `GET /api/v1/calibration-targets` |  | The operator's gate target for a question and its history; none means the default (C3: CI lower bound 0.70 when no held-out rater ceiling exists). |
| `dataworks_set_calibration_target` | `PUT /api/v1/calibration-targets` | `gate_target_write` | Ask to change the gate target for a question. |

## `review` (10)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_preview_calibration_mapping` | `POST /api/v1/calibration-sets/preview` |  | What a calibration mapping yields on a version (counts, sorted-ratings warning, sample rows). |
| `dataworks_list_review_queues` | `GET /api/v1/review-queues` |  | Review queues with kind, state, size, progress and creator. |
| `dataworks_create_review_queue` | `POST /api/v1/review-queues` |  | Create a label-review or calibration-labeling queue with its sampled items. |
| `dataworks_get_review_queue` | `GET /api/v1/review-queues/{queue_id}` |  | One review queue with its progress. |
| `dataworks_close_review_queue` | `POST /api/v1/review-queues/{queue_id}/close` |  | Close a review queue you created: it leaves the reviewer's list and takes no more decisions; its decisions are kept. |
| `dataworks_get_review_rows` | `GET /api/v1/review-queues/{queue_id}/items` |  | A page of a queue's items with row text and the latest decision. |
| `dataworks_review_decide` | `POST /api/v1/review-items/{item_id}/decisions` |  | Accept the model's label or flag the row for the operator. |
| `dataworks_get_review_decisions` | `GET /api/v1/review-items/{item_id}/decisions` |  | An item's decision history, oldest first, with who decided and why. |
| `dataworks_draw_audit` | `POST /api/v1/versions/{version_id}/audit` |  | Draw a version's audit sample (supersedes one in progress). |
| `dataworks_get_audit_status` | `GET /api/v1/versions/{version_id}/audit` |  | A version's audit: none, in_progress or complete, with its result. |

## `generation` (20)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_list_generation_templates` | `GET /api/v1/generation-templates` |  | List generation templates (expand: seed row → new prompt; respond: prompt → answer). |
| `dataworks_save_generation_template` | `POST /api/v1/generation-templates` |  | Create a generation template (version 1). |
| `dataworks_get_generation_template` | `GET /api/v1/generation-templates/{template_id}` |  | One generation template, its body, hash and placeholders. |
| `dataworks_clone_generation_template` | `POST /api/v1/generation-templates/{template_id}/clone` |  | Copy a generation template as the next version, optionally with a changed body or substitutions into it (JSON Pointers, in order). |
| `dataworks_plan_generation` | `POST /api/v1/generation-runs/plan` |  | Dry-run a generation run: held-out status, counts, identities and every refusal. |
| `dataworks_start_generation` | `POST /api/v1/generation-runs` |  | Start a generation run from a version with a held-out split: standard, or a carrier mode over a carrier plan (017; slot carriers holding the slot token zero or twice are dropped and counted). |
| `dataworks_start_steered_pairs` | `POST /api/v1/generation-runs` |  | Start a steered-pair run: two settings differing on exactly ONE SAE feature, the same prompt and seed per pair; a pair is kept only when miLLM reports both settings applied. |
| `dataworks_list_generation_runs` | `GET /api/v1/generation-runs` |  | List generation runs, newest first. |
| `dataworks_archive_generation_run` | `POST /api/v1/generation-runs/{run_id}/archive` |  | Archive a finished generation run: hide it from default lists and pickers. |
| `dataworks_unarchive_generation_run` | `POST /api/v1/generation-runs/{run_id}/unarchive` |  | Unarchive a generation run: list it again. |
| `dataworks_get_generation_run` | `GET /api/v1/generation-runs/{run_id}` |  | One generation run: state, counts by outcome, snapshots, identities, pinning, timing (queued, started, finished, durations), the behaviour it is for, its dataset and templates by name, and the candidate versions built from it. |
| `dataworks_get_generation_records` | `GET /api/v1/generation-runs/{run_id}/records` |  | A page of generation records: requested and reported steering, check, seed, reason. |
| `dataworks_get_generation_pairs` | `GET /api/v1/generation-runs/{run_id}/pairs` |  | A page of the steered pairs a run kept (both sides matched). |
| `dataworks_resume_generation` | `POST /api/v1/generation-runs/{run_id}/resume` |  | Resume a cancelled or failed run with a new job; committed chunks are kept. |
| `dataworks_preview_generation` | `POST /api/v1/generation-runs/preview` |  | Generate for up to 5 prompts or seed rows now (no lease, refuse-load) and show each response with miLLM's reported steering. |
| `dataworks_compare_steering_settings` | `POST /api/v1/steering-settings/compare` |  | Resolve two steering settings and report which SAE feature indices differ. |
| `dataworks_check_judge_independence` | `POST /api/v1/generation-runs/independence-check` |  | Compare the judge's identity with the generator's (model, revision, steering hash). |
| `dataworks_build_generation_candidate` | `POST /api/v1/generation-runs/{run_id}/candidate-build` |  | Build candidate version C = the input version plus every generated row (no model is called; the build binds the run). |
| `dataworks_get_diversity` | `GET /api/v1/versions/{version_id}/diversity` |  | The version's newest diversity report: figures, controls, verdict, method_version. |
| `dataworks_run_diversity_report` | `POST /api/v1/versions/{version_id}/diversity` |  | Start a diversity report against the version's reference (a falling verdict warns and refuses nothing). |

## `exports` (21)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_build_version_files` | `POST /api/v1/versions/{version_id}/publish-builds` |  | Build the files a publish uploads (Parquet, card, manifest) and their hashes. |
| `dataworks_get_publish_build` | `GET /api/v1/publish-builds/{build_id}` |  | A publish build's state, files and hashes. |
| `dataworks_run_publish_checks` | `POST /api/v1/versions/{version_id}/publish-checks` |  | Run the publish checks (licence, terms, personal data, size) for a build and target. |
| `dataworks_get_publish_checks` | `GET /api/v1/publish-check-runs/{check_run_id}` |  | A publish-check run: each check green, amber or red, with the reason. |
| `dataworks_get_card_draft` | `GET /api/v1/versions/{version_id}/card-draft` |  | The dataset card a publish would push, generated from the version's provenance. |
| `dataworks_publish_version` | `POST /api/v1/publishes` | `hub_push` | Publish a version's build to the Hugging Face (HF) Hub; with miforge_set_kind, as a miForge set in a private repository (FR-008.68). |
| `dataworks_push_card` | `POST /api/v1/publishes/{publish_id}/card` | `hub_push` | Push an updated dataset card to a published repository. |
| `dataworks_verify_hub_hashes` | `POST /api/v1/publishes/{publish_id}/reverify` |  | Re-read the published files from the Hub and compare their hashes with the build. |
| `dataworks_list_publishes` | `GET /api/v1/publishes` |  | List publishes with state and verification result. |
| `dataworks_get_publish` | `GET /api/v1/publishes/{publish_id}` |  | One publish: repository, commit, files, hashes, verification. |
| `dataworks_get_version_manifest` | `GET /api/v1/versions/{version_id}/handoff-manifest` |  | The handoff manifest (midataworks.dataset-version/v1) that miStudio and miForge read. |
| `dataworks_export_trl` | `POST /api/v1/exports` |  | Export a version as a TRL training set (local files). |
| `dataworks_export_miforge_set` | `POST /api/v1/exports` |  | Export a version as a miForge set (local files). |
| `dataworks_export_reward_bundle` | `POST /api/v1/exports` |  | Export a reward bundle for TRL's GRPO trainer from a version (the prompt set it serves): reward.py (standard library), the rubric, a config naming the judge, a README and the manifest. |
| `dataworks_list_exports` | `GET /api/v1/exports` |  | List exports with state and files. |
| `dataworks_get_export` | `GET /api/v1/exports/{export_id}` |  | One export: target, state, files and manifest hash. |
| `dataworks_get_licence_table` | `GET /api/v1/licence-table` |  | The licence table: which licences permit a public push, private only, or forbid. |
| `dataworks_get_model_terms` | `GET /api/v1/model-terms/{model_id}` |  | A model's terms notes: whether training on its outputs is permitted. |
| `dataworks_save_config_version` | `POST /api/v1/config-versions` |  | Save a new numbered config version (a selector or grader for miForge sets). |
| `dataworks_list_config_versions` | `GET /api/v1/config-versions` |  | List config versions, one page with the total. |
| `dataworks_get_config_version` | `GET /api/v1/config-versions/{config_id}` |  | One config version and its body. |

## `detector_sets` (27)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_create_detector_set` | `POST /api/v1/detector-sets` |  | Create a detector set: training rows, an in-distribution test, out-of-distribution evaluations and calibration negatives bound to version splits. |
| `dataworks_list_detector_sets` | `GET /api/v1/detector-sets` |  | Detector sets with their role summary, last send state and last result rung. |
| `dataworks_get_detector_set` | `GET /api/v1/detector-sets/{set_id}` |  | One detector set: roles, length profiles, sends. |
| `dataworks_update_detector_set` | `PATCH /api/v1/detector-sets/{set_id}` |  | Change a set. |
| `dataworks_archive_detector_set` | `POST /api/v1/detector-sets/{set_id}/archive` |  | Archive a set. |
| `dataworks_check_detector_set` | `POST /api/v1/detector-sets/{set_id}/checks` |  | Run checks D-1 to D-11 (green, note or refused, each with a reason and next step), and return the label values each mapping must cover and both length profiles (detail=full). |
| `dataworks_plan_leakage_repair` | `POST /api/v1/detector-sets/{set_id}/leakage-repair/plan` |  | The repair for a D-4 refusal (pairs crossing roles): for each role that gives rows up, how many it loses and a one-step drop_leakage_pairs recipe pinned to the leakage report D-4 read. |
| `dataworks_send_detector_set` | `GET /api/v1/publish-builds/{build_id}`<br>`POST /api/v1/detector-sets/{set_id}/send` | `hub_push` | Send the set to miStudio: publish each version to the Hugging Face Hub, download it into miStudio and register every role. |
| `dataworks_send_detector_sets` | `GET /api/v1/publish-builds/{build_id}`<br>`POST /api/v1/detector-sets/send` | `hub_push` | Send several detector sets to miStudio under ONE approval (017): one send per set sharing a send_group_id; a version the sets share is published once. |
| `dataworks_list_detector_sends` | `GET /api/v1/detector-sets/{set_id}/sends` |  | A set's sends, newest first, one page with the total. |
| `dataworks_get_detector_send` | `GET /api/v1/detector-sends/{send_id}` |  | A send's steps (publish, download, register), its checks and notes, and the miStudio run-request skeleton once every role is registered. |
| `dataworks_resume_detector_send` | `POST /api/v1/detector-sends/{send_id}/resume` |  | Resume a failed or cancelled send; recorded steps are never repeated. |
| `dataworks_refresh_probe_results` | `POST /api/v1/detector-sets/{set_id}/results/refresh` |  | Read miStudio's runs, probes and reports for the set into a new snapshot: per-set AUROC with its interval, the rung in miStudio's words, firing rates and caveats. |
| `dataworks_get_probe_results` | `GET /api/v1/detector-sets/{set_id}/results` |  | A stored results snapshot. |
| `dataworks_mark_reward_probe` | `POST /api/v1/reward-marks` |  | Mark a miStudio probe as used as a training reward. |
| `dataworks_list_reward_marks` | `GET /api/v1/reward-marks` |  | The probes marked as a training reward, one page with the total. |
| `dataworks_create_reproduction_link` | `POST /api/v1/reproduction-links` | `gate_target_write` | Link a version split to an evaluation miStudio already recorded for a probe, so the reproduction gate can run for an imported probe (FR-009.77 option (b)). |
| `dataworks_list_reproduction_links` | `GET /api/v1/reproduction-links` |  | Reproduction links with their checks (content or counts only) and the runs that used them, one page with the total. |
| `dataworks_get_reproduction_link` | `GET /api/v1/reproduction-links/{link_id}` |  | One reproduction link: the miStudio figure, every check and the scoring forms. |
| `dataworks_create_agreement_report` | `POST /api/v1/agreement-reports` |  | Compare a probe-verdict run with a judge run on the same rows: two AUROCs with intervals, agreement and kappa. |
| `dataworks_get_agreement_report` | `GET /api/v1/agreement-reports/{report_id}` |  | One agreement report. |
| `dataworks_plan_minimal_pairs` | `POST /api/v1/minimal-pair-chains/plan` |  | Dry run of a minimal-pair chain: every refusal (held-out seeds, a judge that is the generator, no judge, an unknown verdict), the generation plan and the judge. |
| `dataworks_start_minimal_pairs` | `POST /api/v1/minimal-pair-chains` |  | Start a minimal-pair chain: a generation run edits each seed, a build keeps the pairs, a judge run with the pinned rubric labels them, and a build keeps only verified flips with a pair_id. |
| `dataworks_list_minimal_pair_chains` | `GET /api/v1/minimal-pair-chains` |  | Minimal-pair chains, newest first, each with its stages. |
| `dataworks_get_minimal_pair_chain` | `GET /api/v1/minimal-pair-chains/{chain_id}` |  | One chain: its state, the stage it stands at (or failed at, and why), each stage's run, job and version ID, and the counts of verified and unverified pairs. |
| `dataworks_resume_minimal_pair_chain` | `POST /api/v1/minimal-pair-chains/{chain_id}/resume` |  | Resume a failed or cancelled chain at the stage that stopped. |
| `dataworks_cancel_minimal_pair_chain` | `POST /api/v1/minimal-pair-chains/{chain_id}/cancel` |  | Cancel a running chain and the stage that is live. |

## `rule_signals` (13)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_list_validators` | `GET /api/v1/validators` |  | List the validator library: every registered validator version with its reasons, outputs, parameters, superseded_by and how many conformance cases it has. |
| `dataworks_get_validator` | `GET /api/v1/validators/{validator_id}/versions/{version}` |  | One validator version: its declaration and its cases from rule-signal-cases-v1.json. |
| `dataworks_save_rule_set` | `POST /api/v1/rule-sets` |  | Save a rule set. |
| `dataworks_validate_rule_set` | `POST /api/v1/rule-sets/validate` |  | Check a rule set without saving: every problem and every failing case, each with the expected and actual outputs, every signal's verdict and the step that decided. |
| `dataworks_preview_rule_set` | `POST /api/v1/rule-sets/preview` |  | Run a rule set on one text: per signal whether it fired, its spans (in characters, i.e. |
| `dataworks_list_rule_sets` | `GET /api/v1/rule-sets` |  | List rule sets, one row per name with its latest version, sha256, behaviour key, counts and last validation; archived ones only with include_archived. |
| `dataworks_get_rule_set` | `GET /api/v1/rule-sets/{name}` |  | One rule-set version (default the latest): the document, its sha256 (the pin a recipe step uses), its cases, last validation, superseded_by and every build that used it. |
| `dataworks_clone_rule_set` | `POST /api/v1/rule-sets/{name}/clone` |  | Copy a rule-set version to another name and behaviour key, applying substitutions (JSON Pointer -> value) to signals and cases. |
| `dataworks_get_rule_set_difference` | `GET /api/v1/rule-sets/difference` |  | The structured difference between two rule-set versions: each changed path (a JSON Pointer) with its old and new value; signals and cases are matched by id. |
| `dataworks_archive_rule_set` | `POST /api/v1/rule-sets/{name}/archive` |  | Archive a rule set: hide it (every version) from lists. |
| `dataworks_unarchive_rule_set` | `POST /api/v1/rule-sets/{name}/unarchive` |  | Unarchive a rule set: list it again. |
| `dataworks_derive_rule_set` | `POST /api/v1/rule-derivations` |  | Derive a draft rule set from a labelled version (a job; poll dataworks_job_status, then read dataworks_get_rule_derivation). |
| `dataworks_get_rule_derivation` | `GET /api/v1/rule-derivations/{derivation_id}` |  | A rule derivation: its state (measured, low coverage, not measured), held-out precision, recall and coverage per literal and for the draft, the draft document with its provenance, and how many source rows contain each literal. |

## `code_rules` (7)

| Tool | Calls | Waits for | What it does |
|---|---|---|---|
| `dataworks_get_runner_contract` | `GET /api/v1/runner/contract` |  | The code-rule runner contract as this deployment serves it: language level, the modules a rule may import, the limits floor and the limits in force, the count cap, the reasons a call can end with, the diagnostic length, the dry-run rate and text limit, the caps, and whether code rules are switched on. |
| `dataworks_test_code_rule` | `POST /api/v1/code-rules/test` |  | Dry-run a code rule in the isolated runner: its cases and your texts, per function a count or a reason, and - for a function that raised on your own text - the exception type, line and a short message. |
| `dataworks_save_code_rule` | `POST /api/v1/code-rules` | `code_rule_save` | Save a code rule as version n+1 of its name. |
| `dataworks_list_code_rules` | `GET /api/v1/code-rules` |  | List code rules, one row per name: latest version, hash, behaviour key, approval state, functions and last case result; archived ones only with include_archived. |
| `dataworks_get_code_rule` | `GET /api/v1/code-rules/{name}/versions/{version}` |  | One code-rule version: the document, its hash, its cases, the approval state and history, the contract status (failing under a newer contract minor?) and superseded_by. |
| `dataworks_archive_code_rule` | `POST /api/v1/code-rules/{name}/archive` |  | Archive a code rule: hide it (every version) from lists. |
| `dataworks_unarchive_code_rule` | `POST /api/v1/code-rules/{name}/unarchive` |  | Unarchive a code rule: list it again. |

## Reaching the server, and what every answer looks like

- **Address.** The streamable-HTTP endpoint is `/mcp` on the MCP server; off the cluster it is
  the HTTPS host your Ingress publishes for it (`k8s/base/mcp.yaml`). Every request needs `Authorization: Bearer <token>`; one
  without it, or with a wrong one, is `401` (only `/health` is open).
- **One block per answer.** Every tool answers with exactly one content block. A list is one JSON
  array, and an empty result is the block `[]`, never zero blocks.
- **Whole splits** do not pass through MCP: `dataworks_get_split_download_url` returns a signed,
  expiring URL to fetch with a plain GET.
- **Before a build:** `dataworks_search_hf_datasets`, `dataworks_profile_source` (whole-split
  statistics of an imported source) and `dataworks_plan_build` (validity, labelling-window count and
  approval, cell-balancer caps). None writes anything.
- **Data Designer steps:** see `dataworks_howto("data_designer")`. Validation names the Data
  Designer rule that fails; a failed preview reads `status: failed` with Data Designer's message.
- **Detector sets:** a D-4 refusal names `dataworks_plan_leakage_repair`; `dataworks_send_detector_set`
  waits for the publish builds it starts before filing the approval.

## REST contract for proxies

What miStudio's `dataworks_*` proxy (miStudio 034 FR-5 to FR-14) and any other caller rely on
(010 FTDD section 5.6). Additive changes only; checked by
`backend/tests/contract/test_mistudio_proxy_paths_are_served.py`.

| Item | Value |
|---|---|
| Base | Backend under `/api/`, REST under `/api/v1` |
| Health | `GET /api/health`: 200 while the API is up, body `{"status", "dependencies": {name: {"ok", "reason"}}}`; any 2xx is available |
| Success | Plain JSON resources |
| Error | `{"error": {"code", "message", "details"}}` |
| Header | `X-Dataworks-Agent: agent:<name>` (lower case, digits, hyphens; at most 48 characters after `agent:`). This server sends `agent:dataworks-mcp`; miStudio's proxy sends `agent:mistudio-mcp`. A malformed or empty header is refused with `400 INVALID_AGENT_IDENTITY` |
| Pending | `202` with `{"approval_id", "status": "pending", "action", "request_digest", "expires_at", "hint"}` |
| Status | `GET /api/v1/approvals/{id}`: `pending`, `executing`, `executed` (with `result_kind`, `result_id`), `failed` (with `error`), `rejected` (with `reason`), `expired` |
| Refusals | An agent approve or reject is `403 AGENT_CANNOT_DECIDE` |
| Approval actions | `hub_push`, `agent_label_rows`, `version_delete`, `millm_model_load`, `secret_write`, `source_annotate`, `gate_target_write`, `config_write`, `paid_classifier_run` (017: any agent run, start or resume, on a paid classifier endpoint, whatever its size) |
| Counting | The 5,000-row agent labelling window is counted in REST only, per version, never from a caller's number |
| Digest | An approval binds to the exact stored request; a changed request needs a new approval. A send is one approval for the whole send (P-06) |
| Expiry | A pending approval expires after 24 hours |
| OpenAPI | `GET /api/v1/openapi.json` |

### Review queues for applications (miForge, R-03.41; suite tracker 4.1)

The external-queue lifecycle an application codes against. The application identifies itself with
`X-Dataworks-Agent` (miForge sends `agent:miforge`); the queue's `origin_app` is that name.

| Step | Route | Success | Refusals |
|---|---|---|---|
| Create | `POST /api/v1/review-queues` `{"kind": "external", "question", "label_set", "external_ref"?}` | `201` queue, `state: "open"` | `400 ORIGIN_APP_REQUIRED` without the header |
| Post candidates | `POST /api/v1/review-queues/{id}/candidates` `{"candidates": [...]}` (at most 500) | `201 {"items": [{"external_id", "item_id", "created"}]}`; a repeated `external_id` returns its item | `403 ORIGIN_APP_MISMATCH`, `409 QUEUE_CLOSED`, `413 PAYLOAD_TOO_LARGE`, `422 QUEUE_KIND_INVALID` |
| Read back | `GET /api/v1/review-queues/{id}/decisions?external_ids=…&since=…` (no header needed) | `200 {"items": [...]}` with stable decision IDs | `404 QUEUE_NOT_FOUND` |
| Close | `POST /api/v1/review-queues/{id}/close`, body `{}` or `{"reason": "..."}` (at most 2,000 characters) | `200` the queue with `state: "closed"`, `closed_at`, `closed_by`, `closed_by_origin`, `close_reason`. **Idempotent:** closing a closed queue answers `200` with the first close unchanged | `404 QUEUE_NOT_FOUND`; `403 QUEUE_NOT_YOURS` when an agent closes a queue it did not create; `409 AUDIT_IN_PROGRESS` for an audit queue whose audit is running |

A closed queue keeps its items and decisions (read-back still answers), takes no decisions
(`409 QUEUE_CLOSED`) and no candidates, and is left out of the reviewer's list, which asks for
`GET /api/v1/review-queues?state=open`. Without `state` the list returns every queue. There is no
reopen and no delete: close a queue when its work is done and create a new one for new work.

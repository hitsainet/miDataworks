"""The route ledger as data: what every tool calls, what is exempt, and what is still pending.

The design record is 010 FTID section 3.8 (161 tools, 21 exemption entries). This module is the
form the tests check against the LIVE OpenAPI document and the LIVE tool registry (FR-010.44):

- ``EXPECTED_CALLS`` - every registered tool's one documented call: method, path, the tool's
  arguments and the exact payload it must hand the client.
- ``CALLER_ASSERTION_EXEMPT`` - a tool that makes no call, with the reason.
- ``ROUTE_EXEMPT`` - a SERVED route no tool calls, with the reason.
- ``PENDING_ROUTES`` - a route the ledger names whose owning feature has not merged. It must NOT
  be served (once served, it needs its tool and moves to ``EXPECTED_CALLS``), and its tools must
  NOT register (ADR-027: build nothing against a route that is not served).
- ``PENDING_TOOLS_WITHOUT_ROUTE`` - a pending tool that calls an already-served route but needs a
  capability its feature has not delivered.

Three different failures, three different messages (``tests/unit/test_mcp_parity.py``):
a served route with no tool or exemption; a pending route that became served; a tool or
exemption naming a route that is not served and not pending (an unexpected missing route).

Discrepancies between this ledger and FTID section 3.8 (the code wins; recorded in
``0xcc/reviews/010_implementation_controls_2026-10-07.md``): two served list routes the ledger
does not name have tools (``dataworks_list_exports``, ``dataworks_list_config_versions``), and six
served routes the ledger does not name are exempt with reasons below.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

#: Paths here are relative to ``/api/v1`` unless they start with ``/api/``.
API = "/api/v1"


def full(path: str) -> str:
    return path if path.startswith("/api/") else API + path


def normalise(template: str) -> str:
    """``/api/v1/versions/{version_id}`` -> ``/api/v1/versions/{}``: parameter names differ
    between the ledger and the owning feature's code, and the name is not the route."""
    return re.sub(r"\{[^}]*\}", "{}", template)


def template_matches(template: str, concrete: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(template)) + "$"
    return re.match(pattern, concrete) is not None


_SAMPLE_UPLOAD = base64.b64encode(b'{"text": "hello"}\n').decode()

# --------------------------------------------------------------------------------------------
# Tools that are registered now: their one documented call.
# (method, path, tool_kwargs, payload the RecordingClient records)
# --------------------------------------------------------------------------------------------

_P = {"page": 1, "limit": 50}
_ROLES_009 = [
    {
        "role": "train",
        "version_id": "ver_1",
        "split": "train",
        "input_column": "text",
        "label_column": "label",
        "label_mapping": {"a": "positive", "b": "negative"},
    }
]

#: A minimal-pair chain's arguments as a tool takes them, and the body the client must send.
_MP_ARGS: dict[str, Any] = {
    "input_version_id": "ver_1",
    "text_column": "text",
    "seed_splits": ["train"],
    "sample_size": 20,
    "respond_template_id": "gt_1",
    "rubric_id": "rb_1",
    "flip_from": "yes",
    "flip_to": "no",
}
_MP_BODY: dict[str, Any] = dict(_MP_ARGS)

EXPECTED_CALLS: dict[str, tuple[str, str, dict[str, Any], dict[str, Any]]] = {
    # -- core (Foundation 3.7, 5.7, 9.3)
    "dataworks_health": ("GET", "/api/health", {}, {}),
    "dataworks_list_jobs": ("GET", "/jobs", {"state": "active"}, {"state": "active", "kind": None}),
    "dataworks_job_status": ("GET", "/jobs/job_1", {"job_id": "job_1"}, {}),
    "dataworks_cancel_job": (
        "POST",
        "/jobs/job_1/cancel",
        {"job_id": "job_1", "reason": "stop"},
        {"json_body": {"reason": "stop"}},
    ),
    "dataworks_list_approvals": ("GET", "/approvals", {"status": "pending"}, {"status": "pending"}),
    "dataworks_get_approval_status": ("GET", "/approvals/apr_1", {"approval_id": "apr_1"}, {}),
    # -- datasets: 001 sources
    "dataworks_preview_hf_dataset": (
        "POST",
        "/sources/hf/preview",
        {"repo_id": "owner/name", "split": "train", "access_token": "hf_secret"},
        {
            "json_body": {
                "repo_id": "owner/name",
                "config": None,
                "split": "train",
                "revision": None,
                "access_token": "hf_secret",
            }
        },
    ),
    "dataworks_import_hf_dataset": (
        "POST",
        "/sources/hf",
        {"repo_id": "owner/name", "revision": "main", "confirm_large": True},
        {
            "json_body": {
                "repo_id": "owner/name",
                "config": None,
                "split": None,
                "revision": "main",
                "access_token": None,
                "confirm_large": True,
            }
        },
    ),
    "dataworks_upload_file": (
        "POST",
        "/sources/uploads",
        {"filename": "a.jsonl", "content_base64": _SAMPLE_UPLOAD, "split": "test"},
        {
            "files": [("files", ("a.jsonl", b'{"text": "hello"}\n', "application/octet-stream"))],
            "data": {"manifest": json.dumps({"files": [{"name": "a.jsonl", "split": "test"}]})},
        },
    ),
    "dataworks_list_sources": (
        "GET",
        "/sources",
        {"kind": "hf", "page": 2},
        {"kind": "hf", "state": None, "page": 2, "limit": 50},
    ),
    "dataworks_get_source": ("GET", "/sources/src_1", {"source_id": "src_1"}, {}),
    "dataworks_get_source_rows": (
        "GET",
        "/sources/src_1/rows",
        {"source_id": "src_1", "split": "train"},
        {"split": "train", **_P},
    ),
    "dataworks_annotate_source": (
        "POST",
        "/sources/src_1/annotations",
        {"source_id": "src_1", "kind": "licence", "redistribution": "permits", "reason": "card"},
        {
            "json_body": {
                "kind": "licence",
                "redistribution": "permits",
                "value": {},
                "reason": "card",
            }
        },
    ),
    "dataworks_delete_source": (
        "DELETE",
        "/sources/src_1",
        {"source_id": "src_1", "reason": "duplicate"},
        {"json_body": {"reason": "duplicate"}},
    ),
    "dataworks_get_source_meta": ("GET", "/sources/meta", {}, {}),
    # -- datasets: 002
    "dataworks_get_dataset_meta": ("GET", "/datasets/meta", {}, {}),
    "dataworks_list_datasets": (
        "GET",
        "/datasets",
        {"q": "humor"},
        {"q": "humor", "target_type": None, **_P},
    ),
    "dataworks_create_dataset": (
        "POST",
        "/datasets",
        {"name": "Humor", "target_type": "detector"},
        {"json_body": {"name": "Humor", "target_type": "detector", "description": None}},
    ),
    "dataworks_get_dataset": ("GET", "/datasets/ds_1", {"dataset_id": "ds_1"}, {}),
    "dataworks_update_dataset": (
        "PATCH",
        "/datasets/ds_1",
        {"dataset_id": "ds_1", "description": "new"},
        {"json_body": {"description": "new"}},
    ),
    "dataworks_build_version": (
        "POST",
        "/versions",
        {
            "dataset_id": "ds_1",
            "recipe_revision_id": "rrv_1",
            "inputs": [{"kind": "source", "source_id": "src_1"}],
            "seed": 7,
        },
        {
            "json_body": {
                "dataset_id": "ds_1",
                "recipe_revision_id": "rrv_1",
                "inputs": [{"kind": "source", "source_id": "src_1"}],
                "seed": 7,
                "bindings": [],
                "column_roles": {},
            }
        },
    ),
    "dataworks_list_versions": (
        "GET",
        "/versions",
        {"dataset_id": "ds_1"},
        {"dataset_id": "ds_1", "state": None, **_P},
    ),
    "dataworks_get_version": ("GET", "/versions/ver_1", {"version_id": "ver_1"}, {}),
    "dataworks_get_build_manifest": (
        "GET",
        "/versions/ver_1/manifest",
        {"version_id": "ver_1"},
        {},
    ),
    "dataworks_get_version_rows": (
        "GET",
        "/versions/ver_1/rows",
        {
            "version_id": "ver_1",
            "split": "train",
            "where": [{"column": "label", "op": "=", "value": 1}],
        },
        {
            "split": "train",
            "q": None,
            "where": '[{"column": "label", "op": "=", "value": 1}]',
            **_P,
        },
    ),
    "dataworks_explain_row": (
        "GET",
        "/versions/ver_1/rows/history",
        {"version_id": "ver_1", "row_key": "abc"},
        {"row_key": "abc", "q": None},
    ),
    "dataworks_get_drop_log": ("GET", "/versions/ver_1/drop-log", {"version_id": "ver_1"}, {}),
    "dataworks_list_row_events": (
        "GET",
        "/versions/ver_1/events",
        {"version_id": "ver_1", "step_index": 2},
        {"step_index": 2, "kind": None, "reason_code": None, "row_key": None, **_P},
    ),
    "dataworks_get_lineage": ("GET", "/versions/ver_1/lineage", {"version_id": "ver_1"}, {}),
    "dataworks_compare_versions": (
        "GET",
        "/versions/ver_1/compare",
        {"version_id": "ver_1", "with_version_id": "ver_2"},
        {"with": "ver_2"},
    ),
    "dataworks_verify_rebuild": (
        "POST",
        "/versions/ver_1/verify-rebuild",
        {"version_id": "ver_1"},
        {"json_body": None},
    ),
    "dataworks_delete_version": (
        "DELETE",
        "/versions/ver_1",
        {"version_id": "ver_1", "reason": "bad"},
        {"json_body": {"reason": "bad"}},
    ),
    "dataworks_list_recipes": (
        "GET",
        "/recipes",
        {"archived": True},
        {"q": None, "archived": True, **_P},
    ),
    "dataworks_save_recipe": (
        "POST",
        "/recipes",
        {"name": "r", "body": {"steps": []}},
        {"json_body": {"name": "r", "description": None, "body": {"steps": []}, "step_labels": []}},
    ),
    "dataworks_get_recipe": ("GET", "/recipes/rcp_1", {"recipe_id": "rcp_1"}, {}),
    "dataworks_revise_recipe": (
        "POST",
        "/recipes/rcp_1/revisions",
        {"recipe_id": "rcp_1", "body": {"steps": []}, "step_labels": ["a"]},
        {"json_body": {"body": {"steps": []}, "step_labels": ["a"]}},
    ),
    "dataworks_clone_recipe": (
        "POST",
        "/recipes/rcp_1/clone",
        {"recipe_id": "rcp_1", "name": "copy"},
        {"json_body": {"name": "copy", "revision_id": None}},
    ),
    "dataworks_validate_recipe": (
        "POST",
        "/recipes/validate",
        {"body": {"steps": []}},
        {"json_body": {"body": {"steps": []}}},
    ),
    "dataworks_archive_recipe": (
        "POST",
        "/recipes/rcp_1/archive",
        {"recipe_id": "rcp_1"},
        {"json_body": None},
    ),
    # -- operators (003)
    "dataworks_list_operators": (
        "GET",
        "/operators",
        {"provider": "native"},
        {"provider": "native", "kind": None, "state": None, **_P},
    ),
    "dataworks_list_operator_versions": (
        "GET",
        "/operators/native.dedupe",
        {"name": "native.dedupe"},
        {},
    ),
    "dataworks_get_operator": (
        "GET",
        "/operators/dedupe/1.0.0",
        {"name": "dedupe", "version": "1.0.0"},
        {},
    ),
    "dataworks_validate_operator_params": (
        "POST",
        "/operators/dedupe/1.0.0/validate",
        {"name": "dedupe", "version": "1.0.0", "params": {"k": 1}},
        {"json_body": {"params": {"k": 1}}},
    ),
    "dataworks_preview_operator": (
        "POST",
        "/operators/dedupe/1.0.0/preview",
        {"name": "dedupe", "version": "1.0.0", "input": {"version_id": "ver_1"}, "seed": 3},
        {
            "json_body": {
                "params": {},
                "input": {"version_id": "ver_1"},
                "sample_size": None,
                "seed": 3,
            }
        },
    ),
    "dataworks_get_operator_preview": (
        "GET",
        "/operators/previews/prv_1",
        {"preview_id": "prv_1"},
        {},
    ),
    "dataworks_preview_threshold": (
        "POST",
        "/operators/dedupe/1.0.0/statistics",
        {
            "name": "dedupe",
            "version": "1.0.0",
            "input": {"version_id": "ver_1"},
            "sample_size": 100,
        },
        {
            "json_body": {
                "params": {},
                "input": {"version_id": "ver_1"},
                "sample_size": 100,
                "seed": 0,
            }
        },
    ),
    "dataworks_get_allowlist": ("GET", "/operators/allowlist", {}, {}),
    "dataworks_plan_recipe_upgrade": (
        "POST",
        "/operators/upgrade-plan",
        {"recipe_body": {"steps": []}},
        {"json_body": {"recipe_body": {"steps": []}}},
    ),
    # -- settings (Foundation 8.1, 8.2; P-11)
    "dataworks_get_settings": ("GET", "/settings", {}, {}),
    "dataworks_set_hf_token": (
        "PUT",
        "/settings/hf_token",
        {"token": "hf_secret_value"},
        {"json_body": {"value": "hf_secret_value"}},
    ),
    # Operator decision 2026-10-07: agents may clear settings; a secret clear waits (P-11).
    "dataworks_delete_setting": (
        "DELETE",
        "/settings/hf_token",
        {"key": "hf_token"},
        {"json_body": None},
    ),
    "dataworks_set_endpoint_key": (
        "PUT",
        "/endpoint-roles/judge",
        {
            "role": "judge",
            "protocol": "openai",
            "base_url": "http://millm:8000",
            "model_id": "m",
            "api_key": "sk-secret",
        },
        {
            "json_body": {
                "protocol": "openai",
                "base_url": "http://millm:8000",
                "model_id": "m",
                "api_key": "sk-secret",
                "inherit_from_judge": False,
                "use_mode": "own",
            }
        },
    ),
    # -- labeling: the Foundation endpoint-role reads
    "dataworks_list_endpoint_roles": ("GET", "/endpoint-roles", {}, {}),
    "dataworks_fetch_models": (
        "GET",
        "/endpoint-roles/classifier/models",
        {"role": "classifier"},
        {"base_url": None},
    ),
    # -- labeling: feature 005
    "dataworks_list_templates": ("GET", "/decision-templates", {}, {}),
    "dataworks_save_template": (
        "POST",
        "/decision-templates",
        {"name": "t", "body": {"prompt": "p"}},
        {"json_body": {"name": "t", "body": {"prompt": "p"}}},
    ),
    "dataworks_get_template": ("GET", "/decision-templates/dt_1", {"template_id": "dt_1"}, {}),
    "dataworks_clone_template": (
        "POST",
        "/decision-templates/dt_1/clone",
        {"template_id": "dt_1"},
        {"json_body": {"body": None}},
    ),
    "dataworks_list_rubrics": ("GET", "/rubrics", {}, {}),
    "dataworks_save_rubric": (
        "POST",
        "/rubrics",
        {"name": "r", "body": {"criteria": []}},
        {"json_body": {"name": "r", "body": {"criteria": []}}},
    ),
    "dataworks_get_rubric": ("GET", "/rubrics/rb_1", {"rubric_id": "rb_1"}, {}),
    "dataworks_clone_rubric": (
        "POST",
        "/rubrics/rb_1/clone",
        {"rubric_id": "rb_1", "body": {"criteria": [1]}},
        {"json_body": {"body": {"criteria": [1]}}},
    ),
    "dataworks_test_endpoint_role": (
        "POST",
        "/endpoint-roles/judge/test",
        {"role": "judge"},
        {"json_body": None},
    ),
    "dataworks_label_sample": (
        "POST",
        "/labeling/sample",
        {"input_version_id": "ver_1", "role": "classifier", "template_id": "dt_1", "rows": 3},
        {
            "json_body": {
                "input_version_id": "ver_1",
                "role": "classifier",
                "template_id": "dt_1",
                "rows": 3,
                "seed": 0,
            }
        },
    ),
    "dataworks_preview_band": (
        "POST",
        "/labeling/keep-share",
        {"input_version_id": "ver_1", "template_id": "dt_1", "threshold_positive": 0.7},
        {
            "json_body": {
                "input_version_id": "ver_1",
                "role": "classifier",
                "template_id": "dt_1",
                "threshold_positive": 0.7,
                "seed": 0,
            }
        },
    ),
    "dataworks_get_keep_share": ("GET", "/labeling/keep-share/job_1", {"job_id": "job_1"}, {}),
    "dataworks_plan_label_run": (
        "GET",
        "/label-runs/plan",
        {
            "input_version_id": "ver_1",
            "role": "judge",
            "rubric_id": "rb_1",
            "field_map": {"text": "body"},
        },
        # The literal, sorted text: `field_map` sorts first, so the fixture tells sorted from
        # insertion order (control C6 survived a dict that was already alphabetical).
        {
            "request": '{"field_map": {"text": "body"}, "input_version_id": "ver_1", '
            '"role": "judge", "rubric_id": "rb_1", "transport": "single"}'
        },
    ),
    "dataworks_start_label_run": (
        "POST",
        "/label-runs",
        {
            "input_version_id": "ver_1",
            "role": "classifier",
            "template_id": "dt_1",
            "field_map": {"text": "body"},
            "transport": "batch",
        },
        {
            "json_body": {
                "input_version_id": "ver_1",
                "role": "classifier",
                "template_id": "dt_1",
                "field_map": {"text": "body"},
                "transport": "batch",
            }
        },
    ),
    "dataworks_list_millm_probes": ("GET", "/labeling/probes", {}, {}),
    "dataworks_list_label_runs": (
        "GET",
        "/label-runs",
        {"input_version_id": "ver_1"},
        {"input_version_id": "ver_1", "state": None, "kind": None, **_P},
    ),
    "dataworks_get_label_run": ("GET", "/label-runs/lr_1", {"run_id": "lr_1"}, {}),
    "dataworks_get_label_run_labels": (
        "GET",
        "/label-runs/lr_1/labels",
        {"run_id": "lr_1", "outcome": "positive"},
        {"outcome": "positive", "page": 1, "limit": 100},
    ),
    "dataworks_resume_label_run": (
        "POST",
        "/label-runs/lr_1/resume",
        {"run_id": "lr_1"},
        {"json_body": None},
    ),
    "dataworks_rederive_label_run": (
        "POST",
        "/label-runs/lr_1/rederive",
        {"run_id": "lr_1", "threshold_negative": 0.2},
        {"json_body": {"threshold_negative": 0.2}},
    ),
    "dataworks_aggregate_judges": (
        "POST",
        "/label-runs/aggregate",
        {"run_ids": ["lr_1", "lr_2"]},
        {"json_body": {"run_ids": ["lr_1", "lr_2"]}},
    ),
    # -- exports (008)
    "dataworks_build_version_files": (
        "POST",
        "/versions/ver_1/publish-builds",
        {"version_id": "ver_1", "label_column": "label"},
        {"json_body": {"label_column": "label"}},
    ),
    "dataworks_get_publish_build": ("GET", "/publish-builds/bld_1", {"build_id": "bld_1"}, {}),
    "dataworks_run_publish_checks": (
        "POST",
        "/versions/ver_1/publish-checks",
        {"version_id": "ver_1", "build_id": "bld_1", "repo_id": "owner/set"},
        {"json_body": {"build_id": "bld_1", "repo_id": "owner/set", "visibility": "private"}},
    ),
    "dataworks_get_publish_checks": (
        "GET",
        "/publish-check-runs/chk_1",
        {"check_run_id": "chk_1"},
        {},
    ),
    "dataworks_get_card_draft": (
        "GET",
        "/versions/ver_1/card-draft",
        {"version_id": "ver_1", "build_id": "bld_1"},
        {"repo_id": None, "build_id": "bld_1"},
    ),
    "dataworks_publish_version": (
        "POST",
        "/publishes",
        {
            "version_id": "ver_1",
            "build_id": "bld_1",
            "repo_id": "owner/set",
            "visibility": "public",
        },
        {
            "json_body": {
                "version_id": "ver_1",
                "build_id": "bld_1",
                "repo_id": "owner/set",
                "visibility": "public",
                "card_prose": "",
            }
        },
    ),
    "dataworks_push_card": (
        "POST",
        "/publishes/pub_1/card",
        {"publish_id": "pub_1", "card_prose": "Better prose."},
        {"json_body": {"card_prose": "Better prose."}},
    ),
    "dataworks_verify_hub_hashes": (
        "POST",
        "/publishes/pub_1/reverify",
        {"publish_id": "pub_1"},
        {"json_body": None},
    ),
    "dataworks_list_publishes": (
        "GET",
        "/publishes",
        {"repo_id": "owner/set"},
        {"version_id": None, "repo_id": "owner/set", **_P},
    ),
    "dataworks_get_publish": ("GET", "/publishes/pub_1", {"publish_id": "pub_1"}, {}),
    "dataworks_get_version_manifest": (
        "GET",
        "/versions/ver_1/handoff-manifest",
        {"version_id": "ver_1"},
        {},
    ),
    # -- curation: 004 (served 2026-10-07; moved out of PENDING_ROUTES)
    "dataworks_run_profile": (
        "POST",
        "/versions/ver_1/profile",
        {"version_id": "ver_1", "sample_size": 300},
        {"json_body": {"sample_size": 300}},
    ),
    "dataworks_get_profile": ("GET", "/versions/ver_1/profile", {"version_id": "ver_1"}, {}),
    "dataworks_run_shortcut_audit": (
        "POST",
        "/versions/ver_1/shortcut-audit",
        {"version_id": "ver_1", "label_column": "label"},
        {"json_body": {"label_column": "label"}},
    ),
    "dataworks_get_shortcut_audit": (
        "GET",
        "/versions/ver_1/shortcut-audit",
        {"version_id": "ver_1"},
        {"label_column": None},
    ),
    "dataworks_get_shortcut_cells": (
        "GET",
        "/versions/ver_1/shortcut-audit/cells",
        {"version_id": "ver_1", "column": "format", "value": "joke", "label": "humorous"},
        {"column": "format", "value": "joke", "label": "humorous", "page": 0, "limit": 30},
    ),
    "dataworks_run_leakage_check": (
        "POST",
        "/versions/ver_1/leakage",
        {"version_id": "ver_1", "group_column": "pair_id"},
        {"json_body": {"group_column": "pair_id", "threshold": None}},
    ),
    "dataworks_get_leakage_report": ("GET", "/versions/ver_1/leakage", {"version_id": "ver_1"}, {}),
    "dataworks_get_leakage_pairs": (
        "GET",
        "/versions/ver_1/leakage/pairs",
        {"version_id": "ver_1"},
        {"page": 0, "limit": 50},
    ),
    "dataworks_run_contamination_check": (
        "POST",
        "/versions/ver_1/contamination",
        {"version_id": "ver_1", "benchmark_source_ids": ["src_1"]},
        {"json_body": {"benchmark_source_ids": ["src_1"], "n": None}},
    ),
    "dataworks_get_contamination": (
        "GET",
        "/versions/ver_1/contamination",
        {"version_id": "ver_1"},
        {},
    ),
    "dataworks_validate_trl": (
        "POST",
        "/versions/ver_1/trl-validation",
        {"version_id": "ver_1", "target_type": "dpo"},
        {"json_body": {"target_type": "dpo"}},
    ),
    "dataworks_get_warning_levels": ("GET", "/settings/shortcut-level", {}, {}),
    "dataworks_get_dataset_warning_level": (
        "GET",
        "/datasets/ds_1/shortcut-level",
        {"dataset_id": "ds_1"},
        {},
    ),
    "dataworks_list_benchmarks": ("GET", "/curation/benchmarks", {}, {}),
    "dataworks_export_trl": (
        "POST",
        "/exports",
        {"version_id": "ver_1", "trl_type": "dpo"},
        {
            "json_body": {
                "target": "trl",
                "version_id": "ver_1",
                "trl_type": "dpo",
                "format": "parquet",
                "label_column": None,
                "extra_columns": [],
            }
        },
    ),
    "dataworks_export_miforge_set": (
        "POST",
        "/exports",
        {"version_id": "ver_1", "miforge_set_kind": "corpus", "format": "jsonl"},
        {
            "json_body": {
                "target": "miforge_set",
                "version_id": "ver_1",
                "miforge_set_kind": "corpus",
                "format": "jsonl",
                "label_column": None,
                "extra_columns": [],
            }
        },
    ),
    "dataworks_export_reward_bundle": (
        "POST",
        "/exports",
        {"version_id": "ver_1", "extra_columns": ["score"]},
        {
            "json_body": {
                "target": "reward_bundle",
                "version_id": "ver_1",
                "format": "parquet",
                "label_column": None,
                "extra_columns": ["score"],
            }
        },
    ),
    "dataworks_list_exports": (
        "GET",
        "/exports",
        {"version_id": "ver_1"},
        {"version_id": "ver_1", **_P},
    ),
    "dataworks_get_export": ("GET", "/exports/exp_1", {"export_id": "exp_1"}, {}),
    "dataworks_get_licence_table": ("GET", "/licence-table", {}, {}),
    "dataworks_get_model_terms": ("GET", "/model-terms/m1", {"model_id": "m1"}, {}),
    "dataworks_save_config_version": (
        "POST",
        "/config-versions",
        {"kind": "selector", "name": "sel-a", "body": {"x": 1}},
        {"json_body": {"kind": "selector", "name": "sel-a", "body": {"x": 1}, "parent_id": None}},
    ),
    "dataworks_list_config_versions": (
        "GET",
        "/config-versions",
        {"kind": "grader"},
        {"kind": "grader"},
    ),
    "dataworks_get_config_version": ("GET", "/config-versions/cfg_1", {"config_id": "cfg_1"}, {}),
    # -- 009 detector sets (served 2026-10-07, feature 009)
    "dataworks_create_detector_set": (
        "POST",
        "/detector-sets",
        {"name": "humor-set", "roles": _ROLES_009},
        {
            "json_body": {
                "name": "humor-set",
                "description": "",
                "positive_meaning": "",
                "roles": _ROLES_009,
                "monitored_ref": None,
            }
        },
    ),
    "dataworks_list_detector_sets": (
        "GET",
        "/detector-sets",
        {"archived": False},
        {"archived": False, **_P},
    ),
    "dataworks_get_detector_set": ("GET", "/detector-sets/dts_1", {"set_id": "dts_1"}, {}),
    "dataworks_update_detector_set": (
        "PATCH",
        "/detector-sets/dts_1",
        {"set_id": "dts_1", "description": "new"},
        {"json_body": {"description": "new"}},
    ),
    "dataworks_archive_detector_set": (
        "POST",
        "/detector-sets/dts_1/archive",
        {"set_id": "dts_1"},
        {"json_body": None},
    ),
    "dataworks_check_detector_set": (
        "POST",
        "/detector-sets/dts_1/checks",
        {"set_id": "dts_1"},
        {"json_body": None},
    ),
    "dataworks_send_detector_set": (
        "POST",
        "/detector-sets/dts_1/send",
        {"set_id": "dts_1", "namespace": "mistudio"},
        {"json_body": {"repositories": {}, "namespace": "mistudio", "visibility": "private"}},
    ),
    "dataworks_list_detector_sends": (
        "GET",
        "/detector-sets/dts_1/sends",
        {"set_id": "dts_1"},
        {},
    ),
    "dataworks_get_detector_send": ("GET", "/detector-sends/dsn_1", {"send_id": "dsn_1"}, {}),
    "dataworks_resume_detector_send": (
        "POST",
        "/detector-sends/dsn_1/resume",
        {"send_id": "dsn_1"},
        {"json_body": None},
    ),
    "dataworks_refresh_probe_results": (
        "POST",
        "/detector-sets/dts_1/results/refresh",
        {"set_id": "dts_1"},
        {"json_body": None},
    ),
    "dataworks_get_probe_results": (
        "GET",
        "/detector-sets/dts_1/results",
        {"set_id": "dts_1"},
        {"snapshot": "latest"},
    ),
    "dataworks_mark_reward_probe": (
        "POST",
        "/reward-marks",
        {"mistudio_probe_id": "pm_1", "reason": "reward"},
        {"json_body": {"mistudio_probe_id": "pm_1", "reason": "reward"}},
    ),
    "dataworks_list_reward_marks": ("GET", "/reward-marks", {}, {}),
    # 009 FR-009.77 option (b) (2026-10-07): reproduction links.
    "dataworks_create_reproduction_link": (
        "POST",
        "/reproduction-links",
        {
            "mistudio_probe_id": "pm_1",
            "probe_dataset_id": "pmd_1",
            "version_id": "ver_1",
            "split": "test",
            "label_mapping": {"yes": "positive", "no": "negative"},
        },
        {
            "json_body": {
                "mistudio_probe_id": "pm_1",
                "probe_dataset_id": "pmd_1",
                "version_id": "ver_1",
                "split": "test",
                "input_column": None,
                "label_column": None,
                "label_mapping": {"yes": "positive", "no": "negative"},
            }
        },
    ),
    "dataworks_list_reproduction_links": (
        "GET",
        "/reproduction-links",
        {"mistudio_probe_id": "pm_1"},
        {"mistudio_probe_id": "pm_1"},
    ),
    "dataworks_get_reproduction_link": (
        "GET",
        "/reproduction-links/rpl_1",
        {"link_id": "rpl_1"},
        {},
    ),
    "dataworks_create_agreement_report": (
        "POST",
        "/agreement-reports",
        {
            "version_id": "ver_1",
            "split": "test",
            "probe_label_run_id": "lr_p",
            "judge_label_run_id": "lr_j",
            "reference": {"kind": "label_run", "label_run_id": "lr_h"},
        },
        {
            "json_body": {
                "version_id": "ver_1",
                "split": "test",
                "probe_label_run_id": "lr_p",
                "judge_label_run_id": "lr_j",
                "reference": {"kind": "label_run", "label_run_id": "lr_h"},
                "training_version_id": None,
                "send_disagreements_to_review": False,
            }
        },
    ),
    "dataworks_get_agreement_report": (
        "GET",
        "/agreement-reports/agr_1",
        {"report_id": "agr_1"},
        {},
    ),
    # -- 009 minimal pairs as a chain (served 2026-10-07; operator decision 2026-10-07)
    "dataworks_plan_minimal_pairs": (
        "POST",
        "/minimal-pair-chains/plan",
        {**_MP_ARGS, "max_edit_words": 3},
        {"json_body": {**_MP_BODY, "max_edit_words": 3}},
    ),
    "dataworks_start_minimal_pairs": (
        "POST",
        "/minimal-pair-chains",
        {**_MP_ARGS, "seed": 5, "generator_setting": {"kind": "none"}, "judge_seed": 1},
        {
            "json_body": {
                **_MP_BODY,
                "seed": 5,
                "generator_setting": {"kind": "none"},
                "judge_seed": 1,
            }
        },
    ),
    "dataworks_list_minimal_pair_chains": (
        "GET",
        "/minimal-pair-chains",
        {"state": "failed"},
        {"state": "failed", "input_version_id": None, "page": 1, "limit": 50},
    ),
    "dataworks_get_minimal_pair_chain": (
        "GET",
        "/minimal-pair-chains/mpc_1",
        {"chain_id": "mpc_1"},
        {},
    ),
    "dataworks_resume_minimal_pair_chain": (
        "POST",
        "/minimal-pair-chains/mpc_1/resume",
        {"chain_id": "mpc_1"},
        {"json_body": None},
    ),
    "dataworks_cancel_minimal_pair_chain": (
        "POST",
        "/minimal-pair-chains/mpc_1/cancel",
        {"chain_id": "mpc_1"},
        {"json_body": None},
    ),
    # -- 006 calibration (served 2026-10-07, feature 006)
    "dataworks_import_calibration_set": (
        "POST",
        "/calibration-sets/import",
        {
            "version_id": "v1",
            "question": "Funny?",
            "label_set": ["yes", "no"],
            "mapping": {"schema": "m"},
        },
        {
            "json_body": {
                "version_id": "v1",
                "question": "Funny?",
                "label_set": ["yes", "no"],
                "mapping": {"schema": "m"},
            }
        },
    ),
    "dataworks_build_calibration_set": (
        "POST",
        "/calibration-sets/from-review",
        {"queue_id": "rq_1"},
        {"json_body": {"queue_id": "rq_1"}},
    ),
    "dataworks_list_calibration_sets": (
        "GET",
        "/calibration-sets",
        {"version_id": "v1"},
        {"version_id": "v1", **_P},
    ),
    "dataworks_get_calibration_set": ("GET", "/calibration-sets/cs_1", {"set_id": "cs_1"}, {}),
    "dataworks_compute_calibration": (
        "POST",
        "/calibration-records",
        {"label_run_id": "lr_1", "calibration_set_id": "cs_1"},
        {"json_body": {"label_run_id": "lr_1", "calibration_set_id": "cs_1"}},
    ),
    "dataworks_list_calibration_records": (
        "GET",
        "/calibration-records",
        {"labeler": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {
            "labeler": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "question_hash": None,
            **_P,
        },
    ),
    "dataworks_get_calibration": ("GET", "/calibration-records/cr_1", {"record_id": "cr_1"}, {}),
    "dataworks_get_gate_verdict": (
        "GET",
        "/calibration-status",
        {"fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {
            "labeler": None,
            "fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
    ),
    "dataworks_get_calibration_targets": (
        "GET",
        "/calibration-targets",
        {"question_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {"question_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ),
    "dataworks_set_calibration_target": (
        "PUT",
        "/calibration-targets",
        {"question": "Funny?", "target": 0.8},
        {"json_body": {"question": "Funny?", "target": 0.8}},
    ),
    # -- 006 review (served 2026-10-07, feature 006)
    "dataworks_preview_calibration_mapping": (
        "POST",
        "/calibration-sets/preview",
        {
            "version_id": "v1",
            "question": "Funny?",
            "label_set": ["yes", "no"],
            "mapping": {"schema": "m"},
        },
        {
            "json_body": {
                "version_id": "v1",
                "question": "Funny?",
                "label_set": ["yes", "no"],
                "mapping": {"schema": "m"},
            }
        },
    ),
    "dataworks_list_review_queues": (
        "GET",
        "/review-queues",
        {"kind": "audit"},
        {"kind": "audit", "version_id": None, **_P},
    ),
    "dataworks_create_review_queue": (
        "POST",
        "/review-queues",
        {"kind": "label_review", "label_run_id": "lr_1", "size": 20},
        {"json_body": {"kind": "label_review", "label_run_id": "lr_1", "size": 20}},
    ),
    "dataworks_get_review_queue": ("GET", "/review-queues/rq_1", {"queue_id": "rq_1"}, {}),
    "dataworks_get_review_rows": (
        "GET",
        "/review-queues/rq_1/items",
        {"queue_id": "rq_1", "decided": False},
        {"decided": False, **_P},
    ),
    "dataworks_review_decide": (
        "POST",
        "/review-items/ri_1/decisions",
        {"item_id": "ri_1", "decision": "flag", "reason": "unsure"},
        {"json_body": {"decision": "flag", "reason": "unsure"}},
    ),
    "dataworks_get_review_decisions": (
        "GET",
        "/review-items/ri_1/decisions",
        {"item_id": "ri_1"},
        {},
    ),
    "dataworks_draw_audit": (
        "POST",
        "/versions/v1/audit",
        {"version_id": "v1", "size": 60},
        {"json_body": {"size": 60}},
    ),
    "dataworks_get_audit_status": ("GET", "/versions/v1/audit", {"version_id": "v1"}, {}),
    # -- generation: 007 (FTDD 007 section 5.1; FR-007.45)
    "dataworks_list_generation_templates": (
        "GET",
        "/generation-templates",
        {"kind": "respond"},
        {"kind": "respond", **_P},
    ),
    "dataworks_save_generation_template": (
        "POST",
        "/generation-templates",
        {"name": "respond-x", "kind": "respond", "body": {"prompt": "{prompt}"}},
        {"json_body": {"name": "respond-x", "kind": "respond", "body": {"prompt": "{prompt}"}}},
    ),
    "dataworks_get_generation_template": (
        "GET",
        "/generation-templates/gt_1",
        {"template_id": "gt_1"},
        {},
    ),
    "dataworks_clone_generation_template": (
        "POST",
        "/generation-templates/gt_1/clone",
        {"template_id": "gt_1", "body": {"prompt": "Answer: {prompt}"}},
        {"json_body": {"body": {"prompt": "Answer: {prompt}"}}},
    ),
    "dataworks_plan_generation": (
        "POST",
        "/generation-runs/plan",
        {
            "input_version_id": "ver_1",
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 10,
            "target_type": "sft",
            "respond_template_id": "gt_1",
        },
        {
            "json_body": {
                "mode": "standard",
                "input_version_id": "ver_1",
                "prompt_column": "prompt",
                "seed_splits": ["train"],
                "sample_size": 10,
                "respond_template_id": "gt_1",
                "target_type": "sft",
            }
        },
    ),
    "dataworks_start_generation": (
        "POST",
        "/generation-runs",
        {
            "input_version_id": "ver_1",
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 10,
            "target_type": "sft",
            "respond_template_id": "gt_1",
            "n_responses": 2,
        },
        {
            "json_body": {
                "mode": "standard",
                "input_version_id": "ver_1",
                "prompt_column": "prompt",
                "seed_splits": ["train"],
                "sample_size": 10,
                "n_responses": 2,
                "respond_template_id": "gt_1",
                "target_type": "sft",
            }
        },
    ),
    "dataworks_start_steered_pairs": (
        "POST",
        "/generation-runs",
        {
            "input_version_id": "ver_1",
            "prompt_column": "prompt",
            "seed_splits": ["train"],
            "sample_size": 10,
            "respond_template_id": "gt_1",
            "setting_a": {
                "kind": "inline",
                "sae_id": "s",
                "features": [{"index": 1, "strength": 0.0}],
            },
            "setting_b": {
                "kind": "inline",
                "sae_id": "s",
                "features": [{"index": 1, "strength": 6.0}],
            },
            "chosen_side": "b",
        },
        {
            "json_body": {
                "mode": "steered_pairs",
                "input_version_id": "ver_1",
                "prompt_column": "prompt",
                "seed_splits": ["train"],
                "sample_size": 10,
                "respond_template_id": "gt_1",
                "setting_a": {
                    "kind": "inline",
                    "sae_id": "s",
                    "features": [{"index": 1, "strength": 0.0}],
                },
                "setting_b": {
                    "kind": "inline",
                    "sae_id": "s",
                    "features": [{"index": 1, "strength": 6.0}],
                },
                "chosen_side": "b",
                "target_type": "dpo",
            }
        },
    ),
    "dataworks_list_generation_runs": (
        "GET",
        "/generation-runs",
        {"input_version_id": "ver_1"},
        {"input_version_id": "ver_1", "state": None, "mode": None, **_P},
    ),
    "dataworks_get_generation_run": ("GET", "/generation-runs/gr_1", {"run_id": "gr_1"}, {}),
    "dataworks_get_generation_records": (
        "GET",
        "/generation-runs/gr_1/records",
        {"run_id": "gr_1", "outcome": "discarded"},
        {"outcome": "discarded", "stage": None, **_P},
    ),
    "dataworks_get_generation_pairs": (
        "GET",
        "/generation-runs/gr_1/pairs",
        {"run_id": "gr_1", "page": 2},
        {"page": 2, "limit": 50},
    ),
    "dataworks_resume_generation": (
        "POST",
        "/generation-runs/gr_1/resume",
        {"run_id": "gr_1"},
        {"json_body": None},
    ),
    "dataworks_preview_generation": (
        "POST",
        "/generation-runs/preview",
        {"prompts": ["Tell a joke."], "setting": {"kind": "none"}},
        {"json_body": {"prompts": ["Tell a joke."], "setting": {"kind": "none"}}},
    ),
    "dataworks_compare_steering_settings": (
        "POST",
        "/steering-settings/compare",
        {"setting_a": {"kind": "none"}, "setting_b": {"kind": "profile", "profile_name": "p"}},
        {
            "json_body": {
                "setting_a": {"kind": "none"},
                "setting_b": {"kind": "profile", "profile_name": "p"},
            }
        },
    ),
    "dataworks_check_judge_independence": (
        "POST",
        "/generation-runs/independence-check",
        {"generator_setting": {"kind": "none"}},
        {"json_body": {"mode": "standard", "generator_setting": {"kind": "none"}}},
    ),
    "dataworks_build_generation_candidate": (
        "POST",
        "/generation-runs/gr_1/candidate-build",
        {"run_id": "gr_1", "seed": 7},
        {"json_body": {"seed": 7}},
    ),
    "dataworks_get_diversity": (
        "GET",
        "/versions/ver_1/diversity",
        {"version_id": "ver_1", "column": "completion"},
        {"column": "completion"},
    ),
    "dataworks_run_diversity_report": (
        "POST",
        "/versions/ver_1/diversity",
        {"version_id": "ver_1"},
        {"json_body": {}},
    ),
}


#: Tools that make no backend call, with the reason (miStudio's `_CALLER_ASSERTION_EXEMPT`).
CALLER_ASSERTION_EXEMPT: dict[str, str] = {
    "dataworks_howto": (
        "Static guidance served by the MCP server itself; it makes no REST call "
        "(FTID 3.8). Its topics are asserted in tests/unit/mcp/test_core_tools.py."
    ),
}

# --------------------------------------------------------------------------------------------
# Served routes no tool calls.
# --------------------------------------------------------------------------------------------

ROUTE_EXEMPT: dict[tuple[str, str], str] = {
    # 009
    ("DELETE", "/api/v1/reproduction-links/{}"): (
        "Operator housekeeping: removes a link no run has used. A used link is immutable evidence "
        "and refused; an agent that made a wrong link asks the operator, who can see it."
    ),
    (
        "POST",
        "/api/v1/detector-sends/{}/cancel",
    ): "Alias of job cancel: dataworks_cancel_job reaches the same cancellation.",
    # 007: run cancel is an alias of job cancel (dataworks_cancel_job)
    (
        "POST",
        "/api/v1/generation-runs/{}/cancel",
    ): "Alias of job cancel: dataworks_cancel_job reaches the same cancellation.",
    # 006: the application API for miForge (FTDD 006 section 5.6; adopted by 010 FTID 3.8)
    (
        "POST",
        "/api/v1/review-queues/{}/candidates",
    ): "Application API for miForge (R-03.41), not a UI action.",
    (
        "GET",
        "/api/v1/review-queues/{}/decisions",
    ): "Application API for miForge (R-03.41), not a UI action.",
    # 004: warning levels are read-only for agents (P-09); the routes answer an agent 403.
    ("PUT", "/api/v1/settings/shortcut-level"): "Warning levels are read-only for agents (P-09).",
    (
        "PUT",
        "/api/v1/datasets/{}/shortcut-level",
    ): "Warning levels are read-only for agents (P-09).",
    (
        "DELETE",
        "/api/v1/datasets/{}/shortcut-level",
    ): "Warning levels are read-only for agents (P-09).",
    # Foundation and 010
    (
        "POST",
        "/api/v1/jobs/{}/dismiss",
    ): "UI housekeeping of the Jobs list; changes no data (FTID 3.8).",
    (
        "POST",
        "/api/v1/approvals/{}/approve",
    ): "Agents never decide approvals (C6); refused at REST with 403 AGENT_CANNOT_DECIDE.",
    (
        "POST",
        "/api/v1/approvals/{}/reject",
    ): "Agents never decide approvals (C6); refused at REST with 403 AGENT_CANNOT_DECIDE.",
    (
        "GET",
        "/api/v1/agent-access",
    ): "Display-only Agent access card for the Settings screen (P-08).",
    ("GET", "/api/v1/approvals/actions"): (
        "Not in the FTID 3.8 ledger. The banner's legend of action names; an agent receives the "
        "action in every pending body and the full list from dataworks_howto('approvals')."
    ),
    ("POST", "/api/v1/jobs/selftest"): (
        "Not in the FTID 3.8 ledger. Foundation's deployment self-test, an operator diagnostic that "
        "starts a synthetic job and changes no data."
    ),
    ("GET", "/api/v1/settings/{}"): (
        "Not in the FTID 3.8 ledger. One setting of the masked list dataworks_get_settings returns; "
        "a second tool would be a duplicate surface."
    ),
    ("GET", "/api/v1/endpoint-roles/{}"): (
        "Not in the FTID 3.8 ledger. One role of the list dataworks_list_endpoint_roles returns; a "
        "second tool would be a duplicate surface."
    ),
    # Settings PUT is one route for every key: dataworks_set_hf_token calls it for hf_token, and
    # `operator_name` is refused for agents at REST (C5). No exemption is needed for the route.
    # 002
    (
        "GET",
        "/api/v1/recipes/{}/revisions/{}/export",
    ): "File download; the same canonical body is returned by dataworks_get_recipe.",
    (
        "POST",
        "/api/v1/recipes/import",
    ): "Multipart file upload; an agent creates the same recipe from its body with dataworks_save_recipe.",
    (
        "POST",
        "/api/v1/recipes/{}/build",
    ): "Alias of POST /versions with the recipe pre-filled; dataworks_build_version covers it and carries the same gate.",
    (
        "GET",
        "/api/v1/recipe-drafts",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    (
        "POST",
        "/api/v1/recipe-drafts",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    (
        "GET",
        "/api/v1/recipe-drafts/{}",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    (
        "PUT",
        "/api/v1/recipe-drafts/{}",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    (
        "DELETE",
        "/api/v1/recipe-drafts/{}",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    (
        "POST",
        "/api/v1/recipe-drafts/{}/save",
    ): "UI state of the guided New-dataset screen (FR-002.18, FR-002.48).",
    # 003
    (
        "POST",
        "/api/v1/operators/allowlist",
    ): "Agents are read-only on the allowlist (P-09); refused at REST for agent origin.",
    (
        "POST",
        "/api/v1/operators/allowlist/revoke",
    ): "Agents are read-only on the allowlist (P-09); refused at REST for agent origin.",
    (
        "GET",
        "/api/v1/operators/schema-subset",
    ): "Renderer metadata for the settings form; each manifest's params_schema reaches agents through dataworks_get_operator.",
    # 005
    (
        "GET",
        "/api/v1/decision-templates/{}/export",
    ): "File download; dataworks_get_template carries the same body.",
    (
        "POST",
        "/api/v1/decision-templates/import",
    ): "File upload of an exported template; dataworks_save_template creates the same template from its body.",
    (
        "GET",
        "/api/v1/rubrics/{}/export",
    ): "File download; dataworks_get_rubric carries the same body.",
    (
        "POST",
        "/api/v1/rubrics/import",
    ): "File upload of an exported rubric; dataworks_save_rubric creates the same rubric from its body.",
    (
        "POST",
        "/api/v1/label-runs/{}/cancel",
    ): "Alias of job cancel: dataworks_cancel_job reaches the same cancellation.",
    # 008
    (
        "GET",
        "/api/v1/exports/{}/files/{}",
    ): "Binary file download; agents read the export detail, and published copies are on the Hub.",
    (
        "POST",
        "/api/v1/model-terms/{}/notes",
    ): "Operator-only: a note can unlock a public push (008 T16; confirmed S3-08).",
}

#: Routes the app serves outside its OpenAPI paths, with the reason (checked to stay outside).
NOT_IN_SCHEMA: dict[str, str] = {
    "/api/v1/openapi.json": "The schema itself, read by miStudio's route check (034 FR-9).",
    "/internal/ws/emit": "Worker-to-API only; not exposed by the ingress (ADR-008).",
}

# --------------------------------------------------------------------------------------------
# Pending: routes of features not yet merged. (feature, tools or None, exemption reason or None)
# --------------------------------------------------------------------------------------------

_EXEMPT_ALIAS = "Alias of job cancel: dataworks_cancel_job reaches the same cancellation."
_EXEMPT_FILE = "File download or upload; the get and save tools carry the same body."
_EXEMPT_P09 = "Warning levels are read-only for agents (P-09)."  # (004 served: now in ROUTE_EXEMPT)
_EXEMPT_APP = "Application API for miForge (R-03.41), not a UI action."

PENDING_ROUTES: dict[tuple[str, str], tuple[str, tuple[str, ...], str | None]] = {}

#: Pending tools whose route IS served but whose capability waits on a feature.
PENDING_TOOLS_WITHOUT_ROUTE: dict[str, tuple[str, str]] = {
    "dataworks_run_detector_operator": (
        "009",
        "A convenience over POST /versions with a one-step recipe naming 009's detector operator; "
        "009 FTDD section 5.1 says no 009-specific run route exists. 2026-10-07: the operators "
        "now exist (hard_negative_miner@1, feature_filter@1) but POST /versions takes a "
        "recipe_revision_id, so a one-step recipe is a second call (POST /recipes) the ledger's "
        "one-call shape does not allow; agents reach both operators through "
        "dataworks_create_recipe + dataworks_build_version, and probe verdicts and feature tags "
        "through dataworks_start_label_run. Waits for an operator decision on its shape.",
    ),
}


def pending_tools() -> dict[str, str]:
    """tool name -> owning feature, for every tool that must NOT be registered yet."""
    tools = {t: feature for feature, names, _ in PENDING_ROUTES.values() for t in names}
    tools.update({t: feature for t, (feature, _) in PENDING_TOOLS_WITHOUT_ROUTE.items()})
    return tools


#: FTID section 3.8 total (161) plus the two list tools the served code needed (see docstring),
#: plus dataworks_delete_setting (operator decision 2026-10-07), plus dataworks_list_millm_probes
#: (009's probe-verdict protocol, operator decision 2026-10-07: an agent needs a probe ID), plus
#: the six minimal-pair chain tools (009 FR-009.60 - 009.64; operator decision 2026-10-07: the
#: generator is a chain with its own routes, not the one-step ``dataworks_run_detector_operator``).
LEDGER_TOOL_TOTAL = 174

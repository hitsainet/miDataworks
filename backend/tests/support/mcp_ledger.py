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

#: 017 FR-017.20: one census cell a top-up answers.
_CELL_017 = {"genre": "g1", "placement": "start", "framing": "f1", "length_band": "short"}

#: 017 FR-017.11: a recorded definition as dataworks_resolve_behaviour's ``record`` gives it.
_DEFINITION_017 = {
    "key": "pii",
    "definition_version": 2,
    "positive_meaning": "the text shows it",
    "verified": True,
    "source": "catalogue",
    "read_at": "2026-10-24T00:00:00+00:00",
}

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
    # DLP run G9: newest 50, without params and results, unless asked
    "dataworks_list_jobs": (
        "GET",
        "/jobs",
        {"state": "active"},
        {"state": "active", "kind": None, "limit": 50, "detail": "summary"},
    ),
    "dataworks_job_status": ("GET", "/jobs/job_1", {"job_id": "job_1"}, {}),
    "dataworks_cancel_job": (
        "POST",
        "/jobs/job_1/cancel",
        {"job_id": "job_1", "reason": "stop"},
        {"json_body": {"reason": "stop"}},
    ),
    "dataworks_list_approvals": (
        "GET",
        "/approvals",
        {"status": "pending"},
        {"status": "pending", "include_archived": False},
    ),
    "dataworks_get_approval_status": ("GET", "/approvals/apr_1", {"approval_id": "apr_1"}, {}),
    # -- archive (2026-10-20): a reason travels only when given
    "dataworks_archive_approval": (
        "POST",
        "/approvals/apr_1/archive",
        {"approval_id": "apr_1", "reason": "test data"},
        {"json_body": {"reason": "test data"}},
    ),
    "dataworks_unarchive_approval": (
        "POST",
        "/approvals/apr_1/unarchive",
        {"approval_id": "apr_1"},
        {"json_body": {}},
    ),
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
        {
            "repo_id": "owner/name",
            "revision": "main",
            "confirm_large": True,
            "max_rows": 500,
            "data_files": ["data/part-0.parquet"],
        },
        {
            "json_body": {
                "repo_id": "owner/name",
                "config": None,
                "split": None,
                "revision": "main",
                "access_token": None,
                "confirm_large": True,
                "max_rows": 500,
                "data_files": ["data/part-0.parquet"],
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
    # DLP gap G1 (2026-10-21): rows from nothing
    "dataworks_create_index_source": (
        "POST",
        "/sources/index",
        {"rows": 8000, "display_name": "seed"},
        {
            "json_body": {
                "rows": 8000,
                "column": "idx",
                "start": 0,
                "split": "train",
                "display_name": "seed",
            }
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
        {"q": "humor", "target_type": None, "include_archived": False, **_P},
    ),
    "dataworks_archive_dataset": (
        "POST",
        "/datasets/ds_1/archive",
        {"dataset_id": "ds_1", "reason": "e2e test data"},
        {"json_body": {"reason": "e2e test data"}},
    ),
    "dataworks_unarchive_dataset": (
        "POST",
        "/datasets/ds_1/unarchive",
        {"dataset_id": "ds_1"},
        {"json_body": {}},
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
        {"dataset_id": "ds_1", "state": None, "order": "name", "include_archived": False, **_P},
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
            "columns": ["segment", "label"],
        },
        {
            "split": "train",
            "q": None,
            "where": '[{"column": "label", "op": "=", "value": 1}]',
            "columns": "segment,label",
            **_P,
        },
    ),
    # PII run G2 (2026-10-21): discovery and whole-split profiling before a version exists
    "dataworks_search_hf_datasets": (
        "POST",
        "/sources/hf/search",
        {"query": "pii", "licences": ["mit"], "limit": 5},
        {"json_body": {"query": "pii", "licences": ["mit"], "limit": 5}},
    ),
    "dataworks_profile_source": (
        "POST",
        "/sources/src_1/profile",
        {"source_id": "src_1", "value_columns": ["label"], "text_columns": ["text"]},
        {
            "json_body": {
                "split": None,
                "value_columns": ["label"],
                "top": 20,
                "text_columns": ["text"],
            }
        },
    ),
    # PII run G6 (2026-10-21): a build's dry run
    "dataworks_plan_build": (
        "POST",
        "/versions/plan",
        {
            "dataset_id": "ds_1",
            "recipe_revision_id": "rrv_1",
            "inputs": [{"kind": "source", "source_id": "src_1"}],
            "seed": 3,
        },
        {
            "json_body": {
                "dataset_id": "ds_1",
                "recipe_revision_id": "rrv_1",
                "inputs": [{"kind": "source", "source_id": "src_1"}],
                "seed": 3,
                "bindings": [],
                "column_roles": {},
            }
        },
    ),
    # PII run G8 (2026-10-21): a signed, expiring download of one split
    "dataworks_get_split_download_url": (
        "POST",
        "/versions/ver_1/splits/train/download-url",
        {
            "version_id": "ver_1",
            "split": "train",
            "columns": ["segment", "label"],
            "format": "jsonl",
        },
        {"json_body": {"format": "jsonl", "expires_in_s": 3600, "columns": ["segment", "label"]}},
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
        {"name": "r", "body": {"steps": []}, "behaviour_key": "key-one"},
        {
            "json_body": {
                "name": "r",
                "description": None,
                "body": {"steps": []},
                "step_labels": [],
                # 017 FR-017.9: on the named recipe, not in its body
                "behaviour_key": "key-one",
            },
            # DLP run G6: the body is not echoed back unless asked
            "view": "summary",
        },
    ),
    "dataworks_get_recipe": ("GET", "/recipes/rcp_1", {"recipe_id": "rcp_1"}, {}),
    # 017 FR-017.10: the catalogue read (FTID 017 §5.1)
    "dataworks_resolve_behaviour": (
        "GET",
        "/behaviours/key-one/resolve",
        {"behaviour_key": "key-one"},
        {},
    ),
    "dataworks_revise_recipe": (
        "POST",
        "/recipes/rcp_1/revisions",
        {"recipe_id": "rcp_1", "body": {"steps": []}, "step_labels": ["a"]},
        {"json_body": {"body": {"steps": []}, "step_labels": ["a"]}, "view": "summary"},
    ),
    "dataworks_clone_recipe": (
        "POST",
        "/recipes/rcp_1/clone",
        {"recipe_id": "rcp_1", "name": "copy", "behaviour_key": "key-two"},
        {"json_body": {"name": "copy", "revision_id": None, "behaviour_key": "key-two"}},
    ),
    "dataworks_validate_recipe": (
        "POST",
        "/recipes/validate",
        {"recipe_revision_id": "rev_1", "columns": ["text"]},
        {"json_body": {"body": None, "recipe_revision_id": "rev_1", "columns": ["text"]}},
    ),
    "dataworks_archive_recipe": (
        "POST",
        "/recipes/rcp_1/archive",
        {"recipe_id": "rcp_1"},
        {"json_body": None},
    ),
    # -- rule_signals (016 slice 1): the validator library
    "dataworks_list_validators": (
        "GET",
        "/validators",
        {"page": 2, "limit": 5},
        {"page": 2, "limit": 5},
    ),
    "dataworks_get_validator": (
        "GET",
        "/validators/luhn/versions/1",
        {"validator_id": "luhn", "version": 1},
        {},
    ),
    # -- rule sets (016 slice 2)
    "dataworks_save_rule_set": (
        "POST",
        "/rule-sets",
        {"document": {"format": "midataworks.rule-set/v1"}},
        {"json_body": {"document": {"format": "midataworks.rule-set/v1"}}},
    ),
    "dataworks_validate_rule_set": (
        "POST",
        "/rule-sets/validate",
        {"name": "rs-a", "version": 2},
        {"json_body": {"document": None, "name": "rs-a", "version": 2}},
    ),
    "dataworks_preview_rule_set": (
        "POST",
        "/rule-sets/preview",
        {"text": "some text", "name": "rs-a"},
        {
            "json_body": {
                "text": "some text",
                "document": None,
                "name": "rs-a",
                "version": None,
                "element": None,
            }
        },
    ),
    "dataworks_list_rule_sets": (
        "GET",
        "/rule-sets",
        {"page": 2, "limit": 5, "behaviour_key": "key-a", "include_archived": True},
        {"page": 2, "limit": 5, "behaviour_key": "key-a", "include_archived": True},
    ),
    "dataworks_get_rule_set": (
        "GET",
        "/rule-sets/rs-a",
        {"name": "rs-a", "version": 2},
        {"version": 2},
    ),
    "dataworks_clone_rule_set": (
        "POST",
        "/rule-sets/rs-a/clone",
        {
            "name": "rs-a",
            "new_name": "rs-b",
            "new_behaviour_key": "key-b",
            "version": 1,
            "substitutions": [{"pointer": "/description", "value": "x"}],
        },
        {
            "json_body": {
                "version": 1,
                "new_name": "rs-b",
                "new_behaviour_key": "key-b",
                "substitutions": [{"pointer": "/description", "value": "x"}],
                "replacement_cases": [],
            }
        },
    ),
    "dataworks_get_rule_set_difference": (
        "GET",
        "/rule-sets/difference",
        {"a": "rs-a@1", "b": "rs-a@2"},
        {"a": "rs-a@1", "b": "rs-a@2"},
    ),
    "dataworks_archive_rule_set": (
        "POST",
        "/rule-sets/rs-a/archive",
        {"name": "rs-a", "reason": "superseded"},
        {"json_body": {"reason": "superseded"}},
    ),
    "dataworks_derive_rule_set": (
        "POST",
        "/rule-derivations",
        {
            "source_version_id": "v_1",
            "text_column": "text",
            "label_column": "label",
            "positive_values": [1],
            "behaviour_key": "key-a",
            # Review R1 (slices 2-4): every optional argument differs from its default, so a
            # payload that sends a constant instead of the argument is red (it matched before).
            "name": "rs-derived",
            "group_column": "grp",
            "floor": 0.9,
            "max_signals": 5,
            "min_support": 3,
            "held_out_fraction": 0.3,
            "seed": 7,
        },
        {
            "json_body": {
                "source_version_id": "v_1",
                "text_column": "text",
                "label_column": "label",
                "positive_values": [1],
                "behaviour_key": "key-a",
                "name": "rs-derived",
                "group_column": "grp",
                "floor": 0.9,
                "max_signals": 5,
                "min_support": 3,
                "held_out_fraction": 0.3,
                "seed": 7,
            }
        },
    ),
    "dataworks_get_rule_derivation": (
        "GET",
        "/rule-derivations/rdv_0123456789abcdef01234567",
        {"derivation_id": "rdv_0123456789abcdef01234567"},
        {},
    ),
    "dataworks_unarchive_rule_set": (
        "POST",
        "/rule-sets/rs-a/unarchive",
        {"name": "rs-a"},
        {"json_body": {}},
    ),
    # -- code rules (018 slice 3). Every optional argument differs from its default, so a payload
    # that sends a constant instead of the argument is red.
    "dataworks_get_runner_contract": ("GET", "/runner/contract", {}, {}),
    "dataworks_test_code_rule": (
        "POST",
        "/code-rules/test",
        {
            "document": {"runner_contract": "midataworks.runner/v1"},
            "cases": [{"id": "c1"}],
            "name": "cr-a",
            "version": 3,
            "texts": ["a text"],
            "params": {"min": 2},
            "functions": ["f"],
        },
        {
            "json_body": {
                "document": {"runner_contract": "midataworks.runner/v1"},
                "cases": [{"id": "c1"}],
                "name": "cr-a",
                "version": 3,
                "texts": ["a text"],
                "params": {"min": 2},
                "functions": ["f"],
            }
        },
    ),
    "dataworks_save_code_rule": (
        "POST",
        "/code-rules",
        {
            "name": "cr-a",
            "behaviour_key": "key-a",
            "document": {"runner_contract": "midataworks.runner/v1"},
            "cases": [{"id": "c1"}],
        },
        {
            "json_body": {
                "name": "cr-a",
                "behaviour_key": "key-a",
                "document": {"runner_contract": "midataworks.runner/v1"},
                "cases": [{"id": "c1"}],
            }
        },
    ),
    "dataworks_list_code_rules": (
        "GET",
        "/code-rules",
        {"page": 2, "limit": 5, "behaviour_key": "key-a", "include_archived": True},
        {"page": 2, "limit": 5, "behaviour_key": "key-a", "include_archived": True},
    ),
    "dataworks_get_code_rule": (
        "GET",
        "/code-rules/cr-a/versions/3",
        {"name": "cr-a", "version": 3},
        {},
    ),
    "dataworks_archive_code_rule": (
        "POST",
        "/code-rules/cr-a/archive",
        {"name": "cr-a", "reason": "superseded"},
        {"json_body": {"reason": "superseded"}},
    ),
    "dataworks_unarchive_code_rule": (
        "POST",
        "/code-rules/cr-a/unarchive",
        {"name": "cr-a"},
        {"json_body": {}},
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
        {"name": "dedupe", "version": "1.0.0", "params": {"k": 1}, "columns": ["text", "uid"]},
        {"json_body": {"params": {"k": 1}, "columns": ["text", "uid"]}},
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
    # Feature 012 (ENH-003 D5): the runtime environment settings; writes wait (config_write).
    "dataworks_get_environment": ("GET", "/settings/environment", {}, {}),
    "dataworks_set_environment": (
        "PUT",
        "/settings/endpoint_http_timeout_seconds",
        {"key": "endpoint_http_timeout_seconds", "value": "90", "reason": "slow judge"},
        {"json_body": {"value": "90", "reason": "slow judge"}},
    ),
    "dataworks_reset_environment": (
        "DELETE",
        "/settings/endpoint_http_timeout_seconds",
        {"key": "endpoint_http_timeout_seconds", "reason": "back to normal"},
        {"json_body": None, "reason": "back to normal"},
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
    "dataworks_list_rubrics": (
        "GET",
        "/rubrics",
        {"behaviour_key": "pii"},
        {"behaviour_key": "pii"},
    ),
    "dataworks_save_rubric": (
        "POST",
        "/rubrics",
        {"name": "r", "body": {"criteria": []}, "behaviour_key": "pii"},
        {"json_body": {"name": "r", "body": {"criteria": []}, "behaviour_key": "pii"}},
    ),
    "dataworks_set_rubric_behaviour": (
        "PATCH",
        "/rubrics/rb_1",
        {"rubric_id": "rb_1", "behaviour_key": "pii"},
        {"json_body": {"behaviour_key": "pii"}},
    ),
    "dataworks_get_rubric": ("GET", "/rubrics/rb_1", {"rubric_id": "rb_1"}, {}),
    "dataworks_clone_rubric": (
        "POST",
        "/rubrics/rb_1/clone",
        {"rubric_id": "rb_1", "body": {"criteria": [1]}, "behaviour_key": "high-stakes"},
        {"json_body": {"body": {"criteria": [1]}, "behaviour_key": "high-stakes"}},
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
            "behaviour_key": "key-one",
            "target_column": "label",
            "handoff_id": "ho-1",
        },
        # The literal, sorted text: `behaviour_key` sorts first, so the fixture tells sorted from
        # insertion order (control C6 survived a dict that was already alphabetical).
        {
            "request": '{"behaviour_key": "key-one", "field_map": {"text": "body"}, '
            '"handoff_id": "ho-1", "input_version_id": "ver_1", "role": "judge", '
            '"rubric_id": "rb_1", "target_column": "label", "transport": "single"}'
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
            "behaviour_key": "key-one",
            "target_column": "sq_one",
            "handoff_id": "ho-1",
        },
        {
            "json_body": {
                "input_version_id": "ver_1",
                "role": "classifier",
                "template_id": "dt_1",
                "field_map": {"text": "body"},
                "transport": "batch",
                "behaviour_key": "key-one",
                "target_column": "sq_one",
                "handoff_id": "ho-1",
            }
        },
    ),
    "dataworks_list_millm_probes": ("GET", "/labeling/probes", {}, {}),
    "dataworks_list_label_runs": (
        "GET",
        "/label-runs",
        {"input_version_id": "ver_1"},
        {"input_version_id": "ver_1", "state": None, "kind": None, "include_archived": False, **_P},
    ),
    "dataworks_archive_label_run": (
        "POST",
        "/label-runs/lr_1/archive",
        {"run_id": "lr_1", "reason": "test run"},
        {"json_body": {"reason": "test run"}},
    ),
    "dataworks_unarchive_label_run": (
        "POST",
        "/label-runs/lr_1/unarchive",
        {"run_id": "lr_1"},
        {"json_body": {}},
    ),
    "dataworks_get_label_run": ("GET", "/label-runs/lr_1", {"run_id": "lr_1"}, {}),
    "dataworks_get_label_run_labels": (
        "GET",
        "/label-runs/lr_1/labels",
        {"run_id": "lr_1", "outcome": "positive"},
        {"outcome": "positive", "page": 1, "limit": 100},
    ),
    "dataworks_get_label_run_agreement": (
        "GET",
        "/label-runs/lr_1/agreement",
        {
            "run_id": "lr_1",
            "column": "humor",
            "by": ["genre", "kind"],
            "limit": 20,
            "page_token": "o:20",
            "handoff_id": "ho-1",
        },
        {
            "column": "humor",
            "positive_value": None,
            "by": ["genre", "kind"],
            "limit": 20,
            "page_token": "o:20",
            "handoff_id": "ho-1",
        },
    ),
    # 017 FR-017.44 (FTID I17): one tool, one route.
    "dataworks_review_disagreements": (
        "POST",
        "/label-runs/handoffs/ho-1/review",
        {"handoff_id": "ho-1", "column": "sq_one", "by": ["genre"]},
        {"json_body": {"column": "sq_one", "by": ["genre"]}},
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
        # D7 (2026-10-16): the miForge-set delivery, private, with its target and set kind
        {
            "version_id": "ver_1",
            "build_id": "bld_1",
            "repo_id": "owner/set",
            "visibility": "private",
            "miforge_set_kind": "prompt_set",
        },
        {
            "json_body": {
                "version_id": "ver_1",
                "build_id": "bld_1",
                "repo_id": "owner/set",
                "visibility": "private",
                "card_prose": "",
                "target": "miforge_set",
                "miforge_set_kind": "prompt_set",
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
        {"version_id": "ver_1", "label_column": "label", "text_baseline": True},
        {"json_body": {"label_column": "label", "text_baseline": True}, "detail": "summary"},
    ),
    "dataworks_get_shortcut_audit": (
        "GET",
        "/versions/ver_1/shortcut-audit",
        {"version_id": "ver_1", "detail": "full"},
        {"label_column": None, "detail": "full"},
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
        {
            "version_id": "ver_1",
            "rubric_id": "rub_1",
            "judge_model_id": "judge-model",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
        },
        {
            "json_body": {
                "target": "reward_bundle",
                "version_id": "ver_1",
                "rubric_id": "rub_1",
                "judge_model_id": "judge-model",
                "verdict_scores": {"yes": 1.0, "no": 0.0},
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
        {
            "name": "humor-set",
            "roles": _ROLES_009,
            "behaviour_key": "humor",
            "benchmark_source_ids": ["src_b"],
        },
        {
            "json_body": {
                "name": "humor-set",
                "description": "",
                "positive_meaning": "",
                "roles": _ROLES_009,
                "monitored_ref": None,
                "behaviour_key": "humor",
                "benchmark_source_ids": ["src_b"],
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
        {
            "set_id": "dts_1",
            "description": "new",
            "behaviour_key": "pii",
            "behaviour_definition": _DEFINITION_017,
            "benchmark_source_ids": [],
        },
        {
            "json_body": {
                "description": "new",
                "behaviour_key": "pii",
                "behaviour_definition": _DEFINITION_017,
                "benchmark_source_ids": [],
            }
        },
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
        {"json_body": None, "detail": "summary"},
    ),
    # PII run F-4 (2026-10-21): the repair D-4 names
    "dataworks_plan_leakage_repair": (
        "POST",
        "/detector-sets/dts_1/leakage-repair/plan",
        {"set_id": "dts_1"},
        {"json_body": None},
    ),
    "dataworks_send_detector_set": (
        "POST",
        "/detector-sets/dts_1/send",
        {"set_id": "dts_1", "namespace": "mistudio"},
        {"json_body": {"repositories": {}, "namespace": "mistudio", "visibility": "private"}},
    ),
    # 017 TD-017.29: one approval for several sets
    "dataworks_send_detector_sets": (
        "POST",
        "/detector-sets/send",
        {"set_ids": ["dts_1", "dts_2"], "namespace": "mistudio"},
        {
            "json_body": {
                "set_ids": ["dts_1", "dts_2"],
                "repositories": {},
                "namespace": "mistudio",
                "visibility": "private",
            }
        },
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
        {"kind": "audit", "state": "open"},
        {"kind": "audit", "version_id": None, "state": "open", **_P},
    ),
    # suite tracker 4.1 (D2, 2026-10-16): the queue close transition
    "dataworks_close_review_queue": (
        "POST",
        "/review-queues/rq_1/close",
        {"queue_id": "rq_1", "reason": "measurement done"},
        {"json_body": {"reason": "measurement done"}},
    ),
    "dataworks_create_review_queue": (
        "POST",
        "/review-queues",
        {"kind": "label_review", "label_run_id": "lr_1", "size": 20, "behaviour_key": "key-one"},
        {
            "json_body": {
                "kind": "label_review",
                "label_run_id": "lr_1",
                "size": 20,
                "behaviour_key": "key-one",
            }
        },
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
        {
            "name": "respond-x",
            "kind": "respond",
            "body": {"prompt": "{prompt}"},
            "behaviour_key": "key-one",
            "check_rule_sets": [{"name": "rs", "version": 1, "sha256": "a" * 64}],
            "check_phrases": ["a phrase"],
        },
        {
            "json_body": {
                "name": "respond-x",
                "kind": "respond",
                "body": {"prompt": "{prompt}"},
                "behaviour_key": "key-one",
                "check_rule_sets": [{"name": "rs", "version": 1, "sha256": "a" * 64}],
                "check_phrases": ["a phrase"],
            }
        },
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
        {
            "template_id": "gt_1",
            "body": {"prompt": "Answer: {prompt}"},
            "behaviour_key": "key-two",
            "check_phrases": ["a phrase"],
        },
        {
            "json_body": {
                "body": {"prompt": "Answer: {prompt}"},
                "behaviour_key": "key-two",
                "check_phrases": ["a phrase"],
            }
        },
    ),
    # 017 (FTID §5.2): the plan carries a carrier run's fields, a top-up and the rule sets
    "dataworks_plan_generation": (
        "POST",
        "/generation-runs/plan",
        {
            "input_version_id": "ver_1",
            "prompt_column": "text",
            "seed_splits": ["train"],
            "target_type": "detector",
            "mode": "carrier_instructed",
            "behaviour_key": "key-one",
            "generator_model_id": "gen-a",
            "template_map": {"column": "genre", "templates": {"g1": "gt_1"}},
            "rule_sets": [{"name": "rs", "version": 1, "sha256": "a" * 64}],
            "responds_to": {"version_id": "ver_0", "cells": [_CELL_017]},
        },
        {
            "json_body": {
                "mode": "carrier_instructed",
                "input_version_id": "ver_1",
                "prompt_column": "text",
                "seed_splits": ["train"],
                "target_type": "detector",
                "behaviour_key": "key-one",
                "generator_model_id": "gen-a",
                "template_map": {"column": "genre", "templates": {"g1": "gt_1"}},
                "rule_sets": [{"name": "rs", "version": 1, "sha256": "a" * 64}],
                "responds_to": {"version_id": "ver_0", "cells": [_CELL_017]},
            }
        },
    ),
    # 017 I6: the start tool gains `mode` (control: drop it -> this entry fails)
    "dataworks_start_generation": (
        "POST",
        "/generation-runs",
        {
            "input_version_id": "ver_1",
            "prompt_column": "text",
            "seed_splits": ["train"],
            "target_type": "detector",
            "mode": "carrier_slot",
            "behaviour_key": "key-one",
            "generator_model_id": "gen-a",
            "template_map": {"column": "genre", "templates": {"g1": "gt_1"}},
        },
        {
            "json_body": {
                "mode": "carrier_slot",
                "input_version_id": "ver_1",
                "prompt_column": "text",
                "seed_splits": ["train"],
                "target_type": "detector",
                "behaviour_key": "key-one",
                "generator_model_id": "gen-a",
                "template_map": {"column": "genre", "templates": {"g1": "gt_1"}},
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
        {"input_version_id": "ver_1", "state": None, "mode": None, "include_archived": False, **_P},
    ),
    "dataworks_archive_generation_run": (
        "POST",
        "/generation-runs/gr_1/archive",
        {"run_id": "gr_1", "reason": "test run"},
        {"json_body": {"reason": "test run"}},
    ),
    "dataworks_unarchive_generation_run": (
        "POST",
        "/generation-runs/gr_1/unarchive",
        {"run_id": "gr_1"},
        {"json_body": {}},
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
        {
            "generator_setting": {"kind": "none"},
            "role": "classifier",
            "generator_run_ids": ["gr_1", "gr_2"],
        },
        {
            "json_body": {
                "mode": "standard",
                "generator_setting": {"kind": "none"},
                "role": "classifier",
                "generator_run_ids": ["gr_1", "gr_2"],
            }
        },
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
        {"version_id": "ver_1", "breakdown_by": ["generator", "genre"]},
        {"json_body": {"breakdown_by": ["generator", "genre"]}},
    ),
}


#: Reads a tool issues BEFORE its documented call, in order: (method, path, payload). Each is a
#: read the tool's behaviour depends on, asserted with the same exactness as the call itself.
PRE_READS: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    # The operator's upload limit, one source with the backend (2026-10-20): read per call so a
    # file over it is refused before it is sent (tools/datasets.py, upload_limit).
    "dataworks_upload_file": [("GET", "/sources/meta", {})],
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
    # Feature 014 (operator directives 2026-10-20): the Settings PIN and stored-key deletion are
    # operator-only. The routes answer an agent 403 AGENT_FORBIDDEN (test_settings_pin.py).
    (
        "GET",
        "/api/v1/settings/pin",
    ): "The Settings PIN is the operator's lock; agents never use it.",
    (
        "PUT",
        "/api/v1/settings/pin",
    ): "The Settings PIN is the operator's lock; agents never use it.",
    ("POST", "/api/v1/settings/pin/session"): (
        "The Settings PIN is the operator's lock; agents never use it."
    ),
    ("POST", "/api/v1/settings/pin/remove"): (
        "The Settings PIN is the operator's lock; agents never use it."
    ),
    ("DELETE", "/api/v1/endpoint-roles/{}/keys"): (
        "Operator-only: deleting a stored API key; the route answers an agent 403."
    ),
    # Purge (operator decision 2026-10-20): operator-only permanent delete. The routes answer an
    # agent 403 agent_forbidden; tests/integration/test_purge.py asserts no tool reaches them.
    ("POST", "/api/v1/purge"): "operator-only permanent delete",
    ("POST", "/api/v1/purge/plan"): "operator-only permanent delete",
    ("POST", "/api/v1/purge/{}/{}"): "operator-only permanent delete",
    # 015 D56: the session-data mark is the operator's; an agent gets 403 agent_forbidden.
    ("POST", "/api/v1/sources/{}/session-data"): "operator-only session-data mark (015 D56)",
    # Feature 018 (FTDD §6.1): the consumer's resolution route, the approval history, and the
    # operator's own decisions on a hash (FR-018.30: the routes answer an agent 403).
    ("GET", "/api/v1/code-rules/by-hash/{}"): (
        "consumer resolution (miStudio 040); agents use dataworks_get_code_rule"
    ),
    ("GET", "/api/v1/code-rules/approvals"): (
        "the state and history summary are in dataworks_get_code_rule"
    ),
    ("POST", "/api/v1/code-rules/approvals/{}/revoke"): "operator-only (P-09, FR-018.30)",
    ("POST", "/api/v1/code-rules/approvals/{}/approve"): "operator-only (P-09, FR-018.30)",
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
    # PII run G8: the signed URL dataworks_get_split_download_url returns is fetched by the
    # client directly, so a split's bytes never pass through the MCP transport.
    ("GET", "/api/v1/downloads/{}"): (
        "Signed split download: the client fetches the URL dataworks_get_split_download_url "
        "returns; the token is the authorisation."
    ),
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
PENDING_TOOLS_WITHOUT_ROUTE: dict[str, tuple[str, str]] = {}
#: Retired 2026-10-16 (operator decision D6 in the suite tracker): ``dataworks_run_detector_operator``.
#: 009 serves no run route for its detector operators (FTDD 009 section 5.1), and a one-step
#: convenience over ``POST /versions`` would need a second call (``POST /recipes``) the ledger's
#: one-call shape does not allow. Agents reach ``hard_negative_miner@1`` and ``feature_filter@1``
#: through ``dataworks_save_recipe`` + ``dataworks_build_version`` (``docs/detector-sets.md``,
#: "Detector operators from an agent"); probe verdicts and feature tags through
#: ``dataworks_start_label_run``.


def pending_tools() -> dict[str, str]:
    """tool name -> owning feature, for every tool that must NOT be registered yet."""
    tools = {t: feature for feature, names, _ in PENDING_ROUTES.values() for t in names}
    tools.update({t: feature for t, (feature, _) in PENDING_TOOLS_WITHOUT_ROUTE.items()})
    return tools


#: FTID section 3.8 total (161) plus the two list tools the served code needed (see docstring),
#: plus dataworks_delete_setting (operator decision 2026-10-07), plus dataworks_list_millm_probes
#: (009's probe-verdict protocol, operator decision 2026-10-07: an agent needs a probe ID), plus
#: the six minimal-pair chain tools (009 FR-009.60 - 009.64; operator decision 2026-10-07: the
#: generator is a chain with its own routes, not a one-step detector-operator tool),
#: plus dataworks_close_review_queue (suite tracker 4.1, operator decision D2, 2026-10-16), less
#: the retired dataworks_run_detector_operator (operator decision D6, 2026-10-16), plus feature
#: 012's three environment tools (get, set, reset; ENH-003 D5, 2026-10-20), plus the PII run's
#: five gap tools (2026-10-21): dataworks_search_hf_datasets, dataworks_profile_source,
#: dataworks_plan_build, dataworks_get_split_download_url, dataworks_plan_leakage_repair,
#: plus feature 016 slice 1's two validator tools (dataworks_list_validators, dataworks_get_validator)
#: slice 2's nine rule-set tools (save, validate, preview, list, get, clone, difference, archive,
#: unarchive) and slice 4's two derivation tools, plus feature 017 slice 2's catalogue read
#: (dataworks_resolve_behaviour), slice 6's disagreement hand-off (dataworks_review_disagreements)
#: and slice 7's multi-set send (dataworks_send_detector_sets), plus feature 018 slice 3's seven
#: code-rule tools (get_runner_contract, test, save, list, get, archive, unarchive).
LEDGER_TOOL_TOTAL = 216

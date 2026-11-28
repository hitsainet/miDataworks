"""One classification per MCP tool: read, write or destructive, and its title (feature 022).

Claude and ChatGPT decide auto-run against confirmation from ``readOnlyHint`` and
``destructiveHint``; with none set, every tool is a write. This table is the ONE place a tool's
kind is stated (FR-022.15), and :func:`apply_annotations` publishes it server-wide after
registration, like the unknown-argument refusal, so no tool can opt out by omission.

The rules (suite CHAIN_PLAN A13; 022-D4, D5, D21):

- **read:** changes nothing; starts no job, GPU or model call; files no approval; spends no paid
  call; writes no row, file or artifact. A ``POST`` can be read (a plan, a validation, a
  preview that stores nothing); ``tests/unit/mcp/test_read_only_calls.py`` replays every read
  tool's calls and ``tests/integration/test_read_only_routes_write_nothing.py`` proves each such
  ``POST`` writes nothing.
- **destructive:** deletes, purges, clears or cancels, or changes a setting that enables later
  deletion; here also a secret or key overwrite that keeps no old value, and superseding work in
  progress (``tests/unit/mcp/test_destructive_claims.py``).
- **write:** everything else. Archive and unarchive are writes (reversible).

A registered tool with no row refuses the server's start (022-D3): a new tool is classified in
the commit that adds it. ``idempotentHint`` and ``openWorldHint`` stay unset (022-D7): an
unchecked hint is not an honest one.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

Kind = Literal["read", "write", "destructive"]

#: (readOnlyHint, destructiveHint) per kind.
HINTS: dict[str, tuple[bool, bool]] = {
    "read": (True, False),
    "write": (False, False),
    "destructive": (False, True),
}

#: Generated on 2026-11-16 from each tool's client calls (a body calling only ``client.get`` is
#: read, one calling ``client.delete`` destructive, the rest write), then the exceptions of FTDD
#: 022 section 4 by hand, each with its reason. 125 read, 91 write, 11 destructive (search moved
#: to write on 2026-11-16, OR3-022-3); 126 read and 92 write since 021 and MCP gap G-a.
TOOL_CLASS: dict[str, Kind] = {
    "dataworks_aggregate_judges": "write",
    "dataworks_annotate_source": "write",
    "dataworks_archive_approval": "write",
    "dataworks_archive_code_rule": "write",
    "dataworks_archive_dataset": "write",
    "dataworks_archive_detector_set": "write",
    "dataworks_archive_generation_run": "write",
    "dataworks_archive_label_run": "write",
    "dataworks_archive_recipe": "write",
    "dataworks_archive_rule_set": "write",
    "dataworks_build_calibration_set": "write",
    "dataworks_build_generation_candidate": "write",
    "dataworks_build_version": "write",
    "dataworks_build_version_files": "write",
    "dataworks_cancel_job": "destructive",  # cancels work in progress (A13)
    "dataworks_cancel_minimal_pair_chain": "destructive",  # cancels work in progress (A13)
    "dataworks_check_detector_set": "write",  # stores its results
    "dataworks_check_judge_independence": "read",  # a POST that writes nothing
    "dataworks_clone_generation_template": "write",
    "dataworks_clone_recipe": "write",
    "dataworks_clone_rubric": "write",
    "dataworks_clone_rule_set": "write",
    "dataworks_clone_template": "write",
    "dataworks_close_review_queue": "write",
    "dataworks_compare_steering_settings": "read",  # a POST that writes nothing
    "dataworks_compare_versions": "read",
    "dataworks_compute_calibration": "write",
    "dataworks_create_agreement_report": "write",
    "dataworks_create_dataset": "write",
    "dataworks_create_detector_set": "write",
    "dataworks_create_index_source": "write",
    "dataworks_create_reproduction_link": "write",
    "dataworks_create_review_queue": "write",
    "dataworks_delete_setting": "destructive",  # a DELETE route
    "dataworks_delete_source": "destructive",  # a DELETE route
    "dataworks_delete_version": "destructive",  # a DELETE route
    "dataworks_derive_rule_set": "write",
    "dataworks_draw_audit": "destructive",  # supersedes an audit in progress
    "dataworks_explain_row": "read",
    "dataworks_export_miforge_set": "write",
    "dataworks_export_reward_bundle": "write",
    "dataworks_export_trl": "write",
    "dataworks_fetch_models": "read",
    "dataworks_get_agreement_report": "read",
    "dataworks_get_allowlist": "read",
    "dataworks_get_approval_status": "read",
    "dataworks_get_audit_status": "read",
    "dataworks_get_behaviour_status": "read",
    "dataworks_get_build_manifest": "read",
    "dataworks_get_calibration": "read",
    "dataworks_get_calibration_label_agreement": "read",  # a POST that writes nothing
    "dataworks_get_calibration_set": "read",
    "dataworks_get_calibration_targets": "read",
    "dataworks_get_card_draft": "read",
    "dataworks_get_clone_difference": "read",  # a POST that writes nothing
    "dataworks_get_code_rule": "read",
    "dataworks_get_config_version": "read",
    "dataworks_get_contamination": "read",
    "dataworks_get_dataset": "read",
    "dataworks_get_dataset_meta": "read",
    "dataworks_get_dataset_warning_level": "read",
    "dataworks_get_detector_send": "read",
    "dataworks_get_detector_set": "read",
    "dataworks_get_diversity": "read",
    "dataworks_get_drop_log": "read",
    "dataworks_get_environment": "read",
    "dataworks_get_export": "read",
    "dataworks_get_gate_verdict": "read",
    "dataworks_get_generation_pairs": "read",
    "dataworks_get_generation_records": "read",
    "dataworks_get_generation_run": "read",
    "dataworks_get_generation_template": "read",
    "dataworks_get_keep_share": "read",
    "dataworks_get_label_run": "read",
    "dataworks_get_label_run_agreement": "read",
    "dataworks_get_label_run_labels": "read",
    "dataworks_get_leakage_pairs": "read",
    "dataworks_get_leakage_report": "read",
    "dataworks_get_licence_table": "read",
    "dataworks_get_lineage": "read",
    "dataworks_get_minimal_pair_chain": "read",
    "dataworks_get_model_terms": "read",
    "dataworks_get_operator": "read",
    "dataworks_get_operator_preview": "read",
    "dataworks_get_probe_results": "read",
    "dataworks_get_profile": "read",
    "dataworks_get_publish": "read",
    "dataworks_get_publish_build": "read",
    "dataworks_get_publish_checks": "read",
    "dataworks_get_recipe": "read",
    "dataworks_get_reproduction_link": "read",
    "dataworks_get_result_download_url": "read",  # a POST that writes nothing
    "dataworks_get_result_page": "read",
    "dataworks_get_review_decisions": "read",
    "dataworks_get_review_queue": "read",
    "dataworks_get_review_rows": "read",
    "dataworks_get_rubric": "read",
    "dataworks_get_rule_derivation": "read",
    "dataworks_get_rule_set": "read",
    "dataworks_get_rule_set_difference": "read",
    "dataworks_get_runner_contract": "read",
    "dataworks_get_settings": "read",
    "dataworks_get_shortcut_audit": "read",
    "dataworks_get_shortcut_cells": "read",
    "dataworks_get_source": "read",
    "dataworks_get_source_meta": "read",
    "dataworks_get_source_rows": "read",
    "dataworks_get_split_download_url": "read",  # a POST that writes nothing
    "dataworks_get_template": "read",
    "dataworks_get_validator": "read",
    "dataworks_get_version": "read",
    "dataworks_get_version_crosstab": "read",
    "dataworks_get_version_manifest": "write",  # builds the files when none exist (ENH-006 G29)
    "dataworks_get_version_rows": "read",
    "dataworks_get_warning_levels": "read",
    "dataworks_get_wsp_partitions": "read",  # 021: GET /versions/{id}/wsp-partitions
    "dataworks_health": "read",
    "dataworks_howto": "read",  # served by the MCP server; calls nothing
    "dataworks_import_calibration_set": "write",
    "dataworks_import_hf_dataset": "write",
    "dataworks_job_status": "read",
    "dataworks_label_sample": "write",  # calls a labeller, possibly paid (022-D4)
    "dataworks_list_approvals": "read",
    "dataworks_list_benchmarks": "read",
    "dataworks_list_calibration_records": "read",
    "dataworks_list_calibration_sets": "read",
    "dataworks_list_code_rules": "read",
    "dataworks_list_config_versions": "read",
    "dataworks_list_datasets": "read",
    "dataworks_list_detector_sends": "read",
    "dataworks_list_detector_sets": "read",
    "dataworks_list_endpoint_roles": "read",
    "dataworks_list_exports": "read",
    "dataworks_list_generation_runs": "read",
    "dataworks_list_generation_templates": "read",
    "dataworks_list_jobs": "read",
    "dataworks_list_label_runs": "read",
    "dataworks_list_minimal_pair_chains": "read",
    "dataworks_list_miserver_probes": "read",
    "dataworks_list_operator_versions": "read",
    "dataworks_list_operators": "read",
    "dataworks_list_publishes": "read",
    "dataworks_list_recipes": "read",
    "dataworks_list_reproduction_links": "read",
    "dataworks_list_review_queues": "read",
    "dataworks_list_reward_marks": "read",
    "dataworks_list_row_events": "read",
    "dataworks_list_rubrics": "read",
    "dataworks_list_rule_sets": "read",
    "dataworks_list_sources": "read",
    "dataworks_list_templates": "read",
    "dataworks_list_validators": "read",
    "dataworks_list_versions": "read",
    "dataworks_mark_reward_probe": "write",
    "dataworks_plan_build": "read",  # a POST that writes nothing
    "dataworks_plan_generation": "read",  # a POST that writes nothing
    "dataworks_plan_label_run": "read",
    "dataworks_plan_leakage_repair": "read",  # a POST that writes nothing
    "dataworks_plan_minimal_pairs": "read",  # a POST that writes nothing
    "dataworks_plan_recipe_upgrade": "read",  # a POST that writes nothing
    "dataworks_preview_band": "write",  # stores a preview or starts a job
    "dataworks_preview_calibration_mapping": "read",  # a POST that writes nothing
    "dataworks_preview_generation": "write",  # runs a miServer GPU model (022-D4)
    "dataworks_preview_hf_dataset": "write",  # files secret_write when given a token (022-D5)
    "dataworks_preview_operator": "write",  # stores a preview or starts a job
    "dataworks_preview_rule_set": "read",  # a POST that writes nothing
    "dataworks_preview_threshold": "write",  # runs an operator that may call a model (022-D4)
    "dataworks_profile_source": "read",  # a POST that writes nothing
    "dataworks_publish_version": "write",
    # MCP gap G-a (2026-11-25): files a config_write approval; reversible, keeps every key
    "dataworks_propose_endpoint_role": "write",
    "dataworks_push_card": "write",
    "dataworks_rederive_label_run": "write",
    "dataworks_refresh_probe_results": "write",  # stores its results
    "dataworks_request_global_warning_level": "write",
    "dataworks_request_warning_level": "write",
    "dataworks_request_warning_level_clear": "destructive",  # a DELETE route
    "dataworks_reread_behaviour_definition": "write",
    "dataworks_reset_environment": "destructive",  # a DELETE route
    "dataworks_resolve_behaviour": "read",
    "dataworks_resume_detector_send": "write",
    "dataworks_resume_generation": "write",
    "dataworks_resume_label_run": "write",
    "dataworks_resume_minimal_pair_chain": "write",
    "dataworks_review_decide": "write",
    "dataworks_review_disagreements": "write",
    "dataworks_revise_recipe": "write",
    "dataworks_run_contamination_check": "write",
    "dataworks_run_diversity_report": "write",
    "dataworks_run_leakage_check": "write",
    "dataworks_run_profile": "write",  # stores its results
    "dataworks_run_publish_checks": "write",
    "dataworks_run_shortcut_audit": "write",
    "dataworks_run_version_check": "write",
    "dataworks_save_code_rule": "write",
    "dataworks_save_config_version": "write",
    "dataworks_save_generation_template": "write",
    "dataworks_save_recipe": "write",
    "dataworks_save_rubric": "write",
    "dataworks_save_rule_set": "write",
    "dataworks_save_template": "write",
    # Suite A37 (locked): the Hub search is a write, it starts a job in the worker. R3 review fix
    # (OR3-022-3), 2026-11-16; the suite classes the route a write everywhere.
    "dataworks_search_hf_datasets": "write",
    "dataworks_send_detector_set": "write",
    "dataworks_send_detector_sets": "write",
    "dataworks_set_calibration_target": "write",
    "dataworks_set_endpoint_key": "destructive",  # overwrites a stored key
    "dataworks_set_environment": "destructive",  # can lower a retention the prune acts on
    "dataworks_set_hf_token": "destructive",  # overwrites a stored secret
    "dataworks_set_rubric_behaviour": "write",
    "dataworks_start_generation": "write",
    "dataworks_start_label_run": "write",
    "dataworks_start_minimal_pairs": "write",
    "dataworks_start_steered_pairs": "write",
    "dataworks_test_code_rule": "read",  # a POST that writes nothing
    "dataworks_test_endpoint_role": "write",  # calls an endpoint, possibly paid (022-D4)
    "dataworks_unarchive_approval": "write",
    "dataworks_unarchive_code_rule": "write",
    "dataworks_unarchive_dataset": "write",
    "dataworks_unarchive_generation_run": "write",
    "dataworks_unarchive_label_run": "write",
    "dataworks_unarchive_rule_set": "write",
    "dataworks_update_dataset": "write",
    "dataworks_update_detector_set": "write",
    "dataworks_upload_file": "write",
    "dataworks_get_upload_url": "write",  # records a single-use URL (G-3); no approval
    "dataworks_validate_operator_params": "read",  # a POST that writes nothing
    "dataworks_validate_recipe": "read",  # a POST that writes nothing
    "dataworks_validate_rule_set": "read",  # a POST that writes nothing
    "dataworks_validate_trl": "read",  # a POST that writes nothing
    "dataworks_verify_hub_hashes": "write",  # starts a job
    "dataworks_verify_rebuild": "write",  # starts a job
}

#: Words shown in a set form in titles (022-D2).
ACRONYMS: dict[str, str] = {
    "hf": "HF",
    "trl": "TRL",
    "url": "URL",
    "miserver": "miServer",
    "miforge": "miForge",
    "id": "ID",
    "wsp": "WSP",
    "howto": "How-to",
}

#: A title written by hand wins over the generated one.
TITLE_OVERRIDES: dict[str, str] = {}

PREFIX = "dataworks_"


def title_for(name: str) -> str:
    """The human title of a tool: prefix dropped, words split, acronyms set, sentence case."""
    if name in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[name]
    stem = name[len(PREFIX) :] if name.startswith(PREFIX) else name
    words = [ACRONYMS.get(word, word) for word in stem.split("_") if word]
    if words and words[0] == words[0].lower():
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def annotations_for(name: str) -> ToolAnnotations:
    read_only, destructive = HINTS[TOOL_CLASS[name]]
    return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive)


def apply_annotations(mcp: MCPServer) -> int:
    """Give EVERY registered tool its title and hints. Returns the count.

    Refuses the start (``SystemExit``) naming every registered tool with no row, so an
    unclassified tool never reaches a client as an unmarked write (022-D3).
    """
    manager = mcp._tool_manager  # noqa: SLF001 - the SDK offers no public hook
    tools = manager.list_tools()
    missing = sorted(t.name for t in tools if t.name not in TOOL_CLASS)
    if missing:
        raise SystemExit(
            f"MCP tools without an annotation row: {', '.join(missing)}. Add each to "
            "mcp_server/annotations.py TOOL_CLASS (read, write or destructive)."
        )
    for tool in tools:
        tool.title = title_for(tool.name)
        tool.annotations = annotations_for(tool.name)
    return len(tools)

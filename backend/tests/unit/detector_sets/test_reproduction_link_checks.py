"""The link checks and the scoring-form record, as pure functions (009 FR-009.77 option (b)).

The integration file drives them through the route; these pin the decisions one at a time, with
fixtures that DISAGREE where the decision is made (a check that agrees with its fixture by
construction is how two production defects hid on 2026-10-07).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.services.detector_sets import reproduction
from src.services.detector_sets import reproduction_links as rl
from tests.support.fake_mistudio import FIXTURES

VIEWS = {v["id"]: v for v in json.loads((FIXTURES / "probe_datasets_2026-10-07.json").read_text())}
RUN = json.loads((FIXTURES / "run_pmr_a1993af56691.json").read_text())


def view(n_pos: int = 2, n_neg: int = 2, **over: Any) -> dict[str, Any]:
    out = {
        "id": "pmd_x",
        "dataset_id": "d",
        "input_column": "text",
        "label_column": "label",
        "label_mapping": {"humorous": "positive", "not_humorous": "negative"},
        "keyword_filter": None,
        "counts": {
            "kinds": {"plain": n_pos + n_neg},
            "positive": n_pos,
            "negative": n_neg,
            "excluded": 0,
            "filtered_out": 0,
            "unparseable": 0,
        },
    }
    out.update(over)
    return out


OURS = [("a", "positive"), ("b", "negative"), ("c", "positive"), ("d", "negative")]
EVAL = {"n_positive": 2, "n_negative": 2}


def served(pairs: list[tuple[str, str]]) -> rl.Served:
    label = {"positive": "humorous", "negative": "not_humorous"}
    return rl.Served([{"text": t, "label": label[c]} for t, c in pairs], len(pairs))


def test_matching_rows_link_on_content_with_both_hashes_recorded() -> None:
    checks = rl.run_checks(OURS, EVAL, view(), served(OURS), None)
    assert checks["level"] == "content"
    assert checks["content"]["ordered_match"] and checks["content"]["unordered_match"]
    assert checks["content"]["ours_sha256"] == checks["content"]["mistudio_sha256"]


def test_a_row_count_mismatch_is_refused() -> None:
    checks = rl.run_checks(OURS[:3], EVAL, view(), None, "no rows served")
    assert checks["row_count"] == {"ran": True, "ours": 3, "mistudio": 4, "passed": False}
    assert checks["level"] == "refused"


def test_a_class_balance_mismatch_with_equal_counts_is_refused() -> None:
    ours = [("a", "positive"), ("b", "positive"), ("c", "positive"), ("d", "negative")]
    checks = rl.run_checks(ours, EVAL, view(), None, "no rows served")
    assert checks["row_count"]["passed"] is True and checks["class_balance"]["passed"] is False
    assert checks["level"] == "refused"


def test_one_changed_text_is_refused_by_the_content_hash() -> None:
    theirs = [("a", "positive"), ("b", "negative"), ("c", "positive"), ("X", "negative")]
    checks = rl.run_checks(OURS, EVAL, view(), served(theirs), None)
    assert checks["content"]["ran"] is True and checks["content"]["passed"] is False
    assert checks["level"] == "refused"


def test_a_changed_class_on_the_same_text_is_refused_by_the_content_hash() -> None:
    theirs = [("a", "negative"), ("b", "positive"), ("c", "positive"), ("d", "negative")]
    checks = rl.run_checks(OURS, EVAL, view(), served(theirs), None)
    assert checks["level"] == "refused"


def test_order_alone_does_not_refuse() -> None:
    checks = rl.run_checks(OURS, EVAL, view(), served(list(reversed(OURS))), None)
    assert checks["content"]["ordered_match"] is False and checks["level"] == "content"


def test_no_served_rows_is_counts_only_with_the_reason_and_no_hash() -> None:
    checks = rl.run_checks(OURS, EVAL, view(), None, "miStudio refused")
    assert checks["level"] == "counts_only"
    assert checks["content"] == {"ran": False, "reason": "miStudio refused", "order": "file order"}


def test_served_rows_that_do_not_reproduce_the_view_counts_are_not_hashed() -> None:
    other_split = served([("p", "positive")] * 3 + [("q", "negative")] * 3)
    checks = rl.run_checks(OURS, EVAL, view(), other_split, None)
    assert checks["level"] == "counts_only"
    assert checks["content"]["ran"] is False and "ours_sha256" not in checks["content"]


def test_served_rows_miStudio_would_not_parse_are_not_evaluated_rows() -> None:
    rows = served(OURS + [("   ", "positive")])
    v = view(counts={**view()["counts"], "unparseable": 1})
    assert rl.served_evaluated_rows(rows, v) == list(OURS)
    assert rl.run_checks(OURS, EVAL, v, rows, None)["level"] == "content"


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"keyword_filter": {"terms": ["x"]}}, "keyword"),
        ({"counts": {**view()["counts"], "kinds": {"json_messages": 4}}}, "plain-text views"),
        ({"counts": {**view()["counts"], "negative": rl.SAMPLES_ROW_CAP}}, "more than"),
    ],
)
def test_the_content_check_says_why_it_cannot_run(over: dict[str, Any], said: str) -> None:
    reason = rl.content_precondition(view(**over))
    assert reason is not None and said in reason


def test_a_plain_view_needs_no_reason() -> None:
    assert rl.content_precondition(view()) is None


def test_mistudio_label_mapping_reads_values_as_miStudio_does() -> None:
    m = {"1": "positive", "false": "negative", "None": "excluded"}
    assert rl.mistudio_map_label(1, m) == "positive"
    assert rl.mistudio_map_label(False, m) == "negative"
    assert rl.mistudio_map_label("FALSE", m) == "negative"
    assert rl.mistudio_map_label(None, m) == "excluded"
    assert rl.mistudio_map_label(True, {"1": "positive"}) == "positive"
    assert rl.mistudio_map_label("other", m) is None


def test_the_scoring_form_of_a_plain_view_with_no_recorded_render_form_differs() -> None:
    # The 2026-10-07 report (``report_pm_c99519a98e08.json``) predates ``render_form``: the field is
    # ABSENT, so miStudio rendered without the generation prompt, and miLLM renders one user turn
    # with it (2026-10-08 production gate finding).
    form = rl.scoring_form(VIEWS["pmd_c99c8595671d"], RUN, "text")
    assert form["agreement"] == "differs"
    assert form["mistudio"]["input_kinds"] == {"plain": 6600}
    assert form["mistudio"]["scope"] == "all"
    assert form["mistudio"]["template_hash"] == RUN["environment"]["template_hash"]
    assert form["mistudio"]["render_form_recorded"] is False
    assert rl.NOT_RECORDED in form["mistudio"]["described_as"]


def test_a_view_miStudio_read_as_chats_is_recorded_as_differing_from_text() -> None:
    form = rl.scoring_form(VIEWS["pmd_08f7b9bb4778"], None, "text")  # json_messages, stakes
    assert form["agreement"] == "differs" and "json_messages" in form["reason"]
    assert form["mistudio"]["scope"] == "not reported"


def test_a_plain_view_against_a_chat_column_differs() -> None:
    assert rl.scoring_form(view(), RUN, "messages")["agreement"] == "differs"


def test_the_unavailable_refusal_names_both_ways_through() -> None:
    message = reproduction.unavailable_message("pm_1")
    assert "(a) send the detector set" in message
    assert "(b) for a probe miDataworks never sent" in message
    assert "dataworks_create_reproduction_link" in message


def test_the_routes_a_link_reads_are_served_by_miStudio_c829a2cc() -> None:
    """The fixture is miStudio's served OpenAPI, captured from production (ADR-027)."""
    paths = json.loads((FIXTURES / "mistudio_openapi.json").read_text())["paths"]
    for path in (
        "/api/v1/probe-monitors/probes/{probe_id}",
        "/api/v1/probe-monitors/datasets",
        "/api/v1/probe-monitors/runs/{run_id}",
        "/api/v1/datasets/{dataset_id}/samples",
    ):
        assert "get" in paths[path], path

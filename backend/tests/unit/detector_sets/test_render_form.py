"""miStudio's render form, read and compared (2026-10-08 production gate finding).

A link used to say miStudio rendered "no generation prompt" for every probe: a constant citing
miStudio ``c829a2cc``. From ``af7cdad2`` miStudio trains on the form miLLM serves and records it per
probe. These pin the reading of that record and the agreement rule, with miStudio's REAL responses
(captured read-only from production on 2026-10-08):

- served: ``report_pm_1b1f8c50d6d7_2026-10-08.json`` / ``report_pm_f736aa73969d_2026-10-08.json``
  (``render_form`` ``{"generation_prompt": true, "add_special_tokens": false}``,
  ``render_served`` true) and their runs' ``environment.render_form``;
- not recorded, null: ``report_pm_c99519a98e08_2026-10-08.json`` (``render_form: null``,
  ``render_served: false``);
- not recorded, absent: ``report_pm_c99519a98e08.json`` (captured 2026-10-07, before the field);
- explicit false: no production probe records one, so it is the served report with ONE scripted
  change (``generation_prompt: false``, ``render_served: false``).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from src.services.detector_sets import reproduction_links as rl
from tests.support.fake_mistudio import FIXTURES


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


SERVED_REPORT = load("report_pm_1b1f8c50d6d7_2026-10-08.json")
SERVED_RUN = load("run_pmr_59980a0fb155.json")
STAKES_REPORT = load("report_pm_f736aa73969d_2026-10-08.json")
STAKES_RUN = load("run_pmr_1cfe6140efa7.json")
NULL_REPORT = load("report_pm_c99519a98e08_2026-10-08.json")
ABSENT_REPORT = load("report_pm_c99519a98e08.json")
OLD_RUN = load("run_pmr_a1993af56691.json")
PLAIN = {"counts": {"kinds": {"plain": 6600}}}
CHATS = {"counts": {"kinds": {"json_messages": 540}}}
TEXT_ROWS = [("a headline", "positive"), ("another", "negative")]
USER_ENDED = {"user": 2, "assistant": 0, "other": 0}
ASSISTANT_ENDED = {"user": 0, "assistant": 2, "other": 0}


def explicit_false() -> dict[str, Any]:
    probe = copy.deepcopy(SERVED_REPORT["probe"])
    probe["render_form"] = {"generation_prompt": False, "add_special_tokens": False}
    probe["render_served"] = False
    return probe


# --- reading ------------------------------------------------------------------------------------


def test_the_captured_reports_are_the_three_cases() -> None:
    assert SERVED_REPORT["probe"]["render_form"] == rl.SERVED_RENDER_FORM
    assert SERVED_REPORT["probe"]["render_served"] is True
    assert STAKES_REPORT["probe"]["render_form"] == rl.SERVED_RENDER_FORM
    assert NULL_REPORT["probe"]["render_form"] is None
    assert NULL_REPORT["probe"]["render_served"] is False
    assert "render_form" not in ABSENT_REPORT["probe"]
    assert SERVED_RUN["environment"]["render_form"] == rl.SERVED_RENDER_FORM
    assert "render_form" not in OLD_RUN["environment"]


def test_a_served_form_is_read_from_the_probe() -> None:
    got = rl.read_render_form(SERVED_REPORT["probe"], SERVED_RUN)
    assert got == {
        "recorded": True,
        "form": {"generation_prompt": True, "add_special_tokens": False},
        "source": "probe",
        "served": True,
        "mistudio_render_served": True,
    }


def test_null_on_the_probe_is_not_recorded_and_never_served() -> None:
    got = rl.read_render_form(NULL_REPORT["probe"], OLD_RUN)
    assert got["recorded"] is False and got["served"] is False and got["form"] is None


def test_absent_on_the_probe_and_the_run_is_not_recorded() -> None:
    got = rl.read_render_form(ABSENT_REPORT["probe"], OLD_RUN)
    assert got == {
        "recorded": False,
        "form": None,
        "source": None,
        "served": False,
        "mistudio_render_served": None,
    }


def test_absent_on_the_probe_falls_back_to_the_run() -> None:
    probe = {k: v for k, v in SERVED_REPORT["probe"].items() if not k.startswith("render_")}
    got = rl.read_render_form(probe, SERVED_RUN)
    assert got["source"] == "run" and got["served"] is True


def test_null_on_the_probe_is_not_overridden_by_the_run() -> None:
    probe = dict(SERVED_REPORT["probe"], render_form=None, render_served=False)
    assert rl.read_render_form(probe, SERVED_RUN)["recorded"] is False


def test_an_explicit_false_is_recorded_and_not_served() -> None:
    got = rl.read_render_form(explicit_false(), SERVED_RUN)
    assert got["recorded"] is True and got["served"] is False
    assert got["form"] == {"generation_prompt": False, "add_special_tokens": False}


@pytest.mark.parametrize(
    "form",
    [
        {"generation_prompt": True, "add_special_tokens": True},
        {"generation_prompt": "true", "add_special_tokens": False},
        {"generation_prompt": True},
        "served",
    ],
)
def test_only_the_exact_served_form_is_served(form: Any) -> None:
    assert rl.read_render_form({"render_form": form}, None)["served"] is False


@pytest.mark.parametrize("generation_prompt", [False, None, 1])
def test_a_form_without_the_generation_prompt_is_not_served_by_our_own_rule(
    generation_prompt: Any,
) -> None:
    # No ``render_served`` beside it (a run environment, or a miStudio older than the computed
    # field): the rule alone must refuse, never miStudio's flag doing the work (control R3).
    form = {"generation_prompt": generation_prompt, "add_special_tokens": False}
    assert rl.is_served_render_form(form) is False
    assert rl.read_render_form({"render_form": form}, None)["served"] is False


def test_miStudio_saying_not_served_wins_over_the_rule() -> None:
    probe = dict(SERVED_REPORT["probe"], render_served=False)
    assert rl.read_render_form(probe, None)["served"] is False


# --- the agreement ------------------------------------------------------------------------------


def test_served_plain_text_is_equal_by_render_rule_and_never_verified() -> None:
    form = rl.scoring_form(PLAIN, SERVED_RUN, "text", SERVED_REPORT["probe"], USER_ENDED)
    assert form["agreement"] == "equal_by_render_rule"
    assert form["token_ids_compared"] is False
    assert "not verified by token ids" in form["reason"]
    assert form["mistudio"]["render_served"] is True
    assert form["mistudio"]["render_form"] == rl.SERVED_RENDER_FORM
    assert "WITH the generation prompt" in form["mistudio"]["described_as"]
    assert "no generation prompt" not in form["mistudio"]["described_as"]


def test_served_chats_against_parsed_messages_are_equal_by_render_rule() -> None:
    roles = {"user": 3, "assistant": 4, "other": 0}
    form = rl.scoring_form(CHATS, STAKES_RUN, "messages", STAKES_REPORT["probe"], roles)
    assert form["agreement"] == "equal_by_render_rule"
    assert form["token_ids_compared"] is False


def test_not_recorded_plain_text_differs_naming_the_generation_prompt() -> None:
    for report in (NULL_REPORT, ABSENT_REPORT):
        form = rl.scoring_form(PLAIN, OLD_RUN, "text", report["probe"], USER_ENDED)
        assert form["agreement"] == "differs"
        assert rl.NOT_RECORDED in form["reason"] and "2 of the 2 rows" in form["reason"]
        assert form["mistudio"]["render_form_recorded"] is False


def test_not_recorded_assistant_ended_chats_are_not_verified() -> None:
    form = rl.scoring_form(CHATS, OLD_RUN, "messages", NULL_REPORT["probe"], ASSISTANT_ENDED)
    assert form["agreement"] == "not_verified"


def test_an_explicit_false_on_user_ended_rows_differs() -> None:
    form = rl.scoring_form(PLAIN, SERVED_RUN, "text", explicit_false(), USER_ENDED)
    assert form["agreement"] == "differs" and "generation_prompt is False" in form["reason"]


def test_an_explicit_false_on_assistant_ended_rows_is_equal_by_render_rule() -> None:
    form = rl.scoring_form(CHATS, SERVED_RUN, "messages", explicit_false(), ASSISTANT_ENDED)
    assert form["agreement"] == "equal_by_render_rule"


def test_a_second_bos_differs_even_with_the_generation_prompt() -> None:
    probe = {"render_form": {"generation_prompt": True, "add_special_tokens": True}}
    form = rl.scoring_form(PLAIN, SERVED_RUN, "text", probe, USER_ENDED)
    assert form["agreement"] == "differs" and "add_special_tokens is True" in form["reason"]


def test_a_served_probe_against_text_from_decoded_chats_still_differs() -> None:
    # miStudio decoded JSON chats (``json_messages``); an unparsed JSON-string column would go to
    # miLLM as one user turn of text: different inputs whatever the render form.
    form = rl.scoring_form(CHATS, STAKES_RUN, "text", STAKES_REPORT["probe"], USER_ENDED)
    assert form["agreement"] == "differs" and "json_messages" in form["reason"]


def test_a_served_probe_on_plain_text_against_a_chat_column_still_differs() -> None:
    form = rl.scoring_form(PLAIN, SERVED_RUN, "messages", SERVED_REPORT["probe"], USER_ENDED)
    assert form["agreement"] == "differs"


@pytest.mark.parametrize(
    ("probe", "roles"),
    [
        (SERVED_REPORT["probe"], USER_ENDED),
        (STAKES_REPORT["probe"], ASSISTANT_ENDED),
        (NULL_REPORT["probe"], USER_ENDED),
        (NULL_REPORT["probe"], ASSISTANT_ENDED),
        (explicit_false(), ASSISTANT_ENDED),
    ],
)
def test_no_scoring_form_ever_claims_token_ids_were_compared(
    probe: dict[str, Any], roles: dict[str, int]
) -> None:
    form = rl.scoring_form(CHATS, SERVED_RUN, "messages", probe, roles)
    assert form["token_ids_compared"] is False
    assert form["agreement"] != "verified"
    assert "verified by token ids" not in form["reason"].replace("not verified by token ids", "")


def test_last_roles_counts_text_as_user_ended_and_chats_by_their_last_turn() -> None:
    rows = [
        ("plain", "positive"),
        ([{"role": "user", "content": "x"}], "positive"),
        ([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], "negative"),
        ([{"role": "system", "content": "s"}], "negative"),
        ([], "negative"),
    ]
    assert rl.last_roles(rows) == {"user": 2, "assistant": 1, "other": 2}


# --- old links ----------------------------------------------------------------------------------


OLD_FORM = {
    "mistudio": {"described_as": "... no generation prompt (miStudio ... at c829a2cc)"},
    "millm": {"input_form": "text"},
    "agreement": "not_verified",
    "reason": "Both read one user turn per row.",
}


def test_an_old_link_whose_run_recorded_the_served_form_gets_a_note_and_keeps_its_text() -> None:
    out = rl.scoring_form_out(OLD_FORM, {"run_environment": SERVED_RUN["environment"]})
    assert out["note"].startswith("This description was stored before")
    assert {k: v for k, v in out.items() if k != "note"} == OLD_FORM


def test_an_old_link_whose_run_recorded_nothing_is_returned_as_stored() -> None:
    assert rl.scoring_form_out(OLD_FORM, {"run_environment": OLD_RUN["environment"]}) == OLD_FORM
    assert rl.scoring_form_out(OLD_FORM, {}) == OLD_FORM


def test_a_new_link_is_never_annotated() -> None:
    new = rl.scoring_form(PLAIN, SERVED_RUN, "text", SERVED_REPORT["probe"], USER_ENDED)
    assert rl.scoring_form_out(new, {"run_environment": SERVED_RUN["environment"]}) == new

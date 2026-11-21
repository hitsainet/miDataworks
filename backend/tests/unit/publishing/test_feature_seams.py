"""The seam to features 004, 005 and 006, in both directions (008 FTASKS 6.2a; the orchestrator's
rule that a missing check never passes).

- absent owner → ``not_checked`` (and ``checks.py`` turns that into amber, see test_checks.py);
- present owner → its answer, from the function the owner's FTDD names, with the payload and the
  call count asserted against a recording stub;
- an owner whose own import is broken raises: only the module's ABSENCE means "not built".
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from src.services.publishing import checks, feature_seams
from src.services.publishing.feature_seams import CHECKED, NOT_CHECKED


def test_the_owners_are_not_built_yet() -> None:
    """A dated reading (2026-10-07). 006 landed its three modules and filled 005's labeler
    identity (``labeling.identity``); 004 landed too (feature 004 branch), and its real answers are
    tested end to end in tests/integration/curation/test_008_seam_publish.py."""
    assert feature_seams.load_owner(feature_seams.CURATION_API) is not None
    for module in (
        feature_seams.AUDIT_SERVICE,
        feature_seams.CALIBRATION_STATUS,
        feature_seams.EFFECTIVE_LABEL,
        feature_seams.LABELER_IDENTITY,
    ):
        assert feature_seams.load_owner(module) is not None, module


def test_absent_owners_report_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The absent path, kept now that 006 is built: an owner that cannot be loaded is not_checked."""
    monkeypatch.setattr(feature_seams, "load_owner", lambda relative: None)
    assert (
        feature_seams.check_leakage(
            [("v", "train")], held_out=["test"], group_column=None, session=None
        ).status
        == NOT_CHECKED
    )
    assert (
        feature_seams.evaluate_warnings([("v", "train")], label_column="label", session=None).status
        == NOT_CHECKED
    )
    assert feature_seams.validate_trl("v", "dpo", session=None).status == NOT_CHECKED
    assert feature_seams.audit_status("v", session=None).status == NOT_CHECKED
    cal = feature_seams.calibration_status("f" * 64, session=None)
    assert cal.status == NOT_CHECKED and cal.recorded is False
    assert feature_seams.labelers_for(["run_1"], session=None).status == NOT_CHECKED
    assert feature_seams.labelers_for([], session=None).status == CHECKED
    assert feature_seams.resolve_effective_labels("v", None, None, ["k"]) is None


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []


@pytest.fixture
def curation(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    package = types.ModuleType("src.services.curation")
    api = types.ModuleType("src.services.curation.api")

    def check_leakage(inputs: Any, *, group_column: Any, session: Any) -> Any:
        rec.calls.append(
            ("check_leakage", (inputs,), {"group_column": group_column, "session": session})
        )
        return types.SimpleNamespace(
            exact_pairs={"train|test": 2}, near_pairs={"train|validation": 9}, group_pairs={}
        )

    def evaluate_warnings(inputs: Any, *, label_column: Any, session: Any) -> Any:
        rec.calls.append(
            ("evaluate_warnings", (inputs,), {"label_column": label_column, "session": session})
        )
        return types.SimpleNamespace(
            warnings=[{"column": "source_label", "figure": 0.885}], invalid=[]
        )

    def validate_trl(version_id: Any, target_type: Any, *, session: Any) -> Any:
        rec.calls.append(("validate_trl", (version_id, target_type), {"session": session}))
        return types.SimpleNamespace(valid=False, failures=["row 3: chosen is empty"])

    api.check_leakage = check_leakage  # type: ignore[attr-defined]
    api.evaluate_warnings = evaluate_warnings  # type: ignore[attr-defined]
    api.validate_trl = validate_trl  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.services.curation", package)
    monkeypatch.setitem(sys.modules, "src.services.curation.api", api)
    return rec


def test_checks_call_004s_two_functions_once_each_with_the_payload(curation: Recorder) -> None:
    session = object()
    inputs = [("v1", "train"), ("v1", "test")]
    leakage, warnings = checks.gather_curation_findings(
        inputs, held_out=["test"], group_column=None, label_column="label", session=session
    )
    names = [c[0] for c in curation.calls]
    assert names == ["check_leakage", "evaluate_warnings"]
    assert curation.calls[0][1] == (inputs,) and curation.calls[0][2] == {
        "group_column": None,
        "session": session,
    }
    assert curation.calls[1][1] == (inputs,) and curation.calls[1][2] == {
        "label_column": "label",
        "session": session,
    }
    assert (
        leakage.status == CHECKED and leakage.crossing_pairs == 2
    ), "only pairs touching a held-out split"
    assert warnings.status == CHECKED and warnings.warnings[0]["column"] == "source_label"


def test_a_present_validator_is_obeyed(curation: Recorder) -> None:
    finding = feature_seams.validate_trl("v1", "dpo", session=None)
    assert finding.status == CHECKED and finding.valid is False
    assert finding.failures == ["row 3: chosen is empty"]


def test_a_broken_owner_import_is_not_mistaken_for_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    real = importlib.import_module

    def broken(name: str, *args: Any) -> Any:
        if name == "src.services.curation.api":
            raise ModuleNotFoundError("No module named 'sklearn_extra'", name="sklearn_extra")
        return real(name, *args)

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError):
        feature_seams.load_owner(feature_seams.CURATION_API)

"""What the brand guard ENFORCES and what it only REPORTS (feature 020; FPRD Q4 and PD-7, accepted
by R17; FTID §7.5; FTASKS 17.1, 17.2).

Read surfaces are enforced; identifiers are reported. An identifier is a token a program reads, not
a sentence a person does: a whole string literal shaped like one (``"is_millm"``, a wire code, a
task name, a kind with its version), or the same shape inside a backtick code span in prose
(a description naming a code). A token that IS the old name in a display form (any capital:
``miLLM``, ``MiStudio``, ``MILLM``) is a read surface wherever it stands.

Allowances still come from the export only: entries in force, env, repo and host names in
transition, active allow guards. The identifier class is a scope, not an allowance: its hits are
counted and printed in every mode, and it never applies to manifest keys and values or to MCP tool
names, which are read as they stand.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.support import legacy_identifiers as li
from tests.unit.brand import scan

DENY = scan.policy()["deny"]


def classes(text: str, **scope: bool) -> tuple[list[str], list[str]]:
    return li.classified_hits(text, li.load(), DENY, **scope)


#: One table, three readers (this loader, the Vitest loader, check_published.py's twin).
CASES = json.loads((Path(__file__).parent / "scope_cases.json").read_text())["cases"]


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['text'][:40]}|{c['whole']}|{c['spans']}" for c in CASES]
)
def test_identifier_tokens_are_reported_and_read_text_is_enforced(case: dict[str, Any]) -> None:
    got = classes(case["text"], whole=case["whole"], code_spans=case["spans"])
    assert got == (case["read"], case["identifier"])


def test_the_case_table_covers_every_branch_of_the_rule() -> None:
    """Fail closed: a trimmed table would leave a branch of the rule unheld."""
    assert len(CASES) >= 25
    kinds = {
        "whole identifier": any(
            c["whole"] and c["identifier"] and "`" not in c["text"] for c in CASES
        ),
        "display form read": any(c["text"] in ("miLLM", "miStudio", "MILLM") for c in CASES),
        "code span identifier": any("`" in c["text"] and c["identifier"] for c in CASES),
        "code span display form": any(c["text"] == "see `miLLM`" for c in CASES),
        "allowed by the register": any(not c["read"] and not c["identifier"] for c in CASES),
        "name read as it stands": any(
            not c["whole"] and c["read"] and "`" not in c["text"] for c in CASES
        ),
        "spans off": any(not c["spans"] for c in CASES),
    }
    assert all(kinds.values()), kinds


def test_the_published_page_scanner_gives_the_same_answers() -> None:
    """``manual/scripts/check_published.py`` keeps a standard-library twin of the rule (it reads
    lines of prose: never a whole literal). Held to this loader on every case text."""
    from tests.unit.test_manual_publish import scanner

    twin = scanner()
    _, allow, deny = twin.brand_rules(scan.REPO)
    for case in CASES:
        expected = li.classified_hits(case["text"], li.load(), DENY, whole=False)
        assert twin.classify(case["text"], allow, deny) == expected, case["text"]


def test_the_classes_partition_the_unallowed_hits() -> None:
    """Nothing is dropped: read + identifier is exactly what the unscoped scan finds."""
    export = li.load()
    for text in (
        "is_millm",
        "refuses with `PROBE_ENDPOINT_NOT_MILLM` when miLLM is absent; see miStudio",
        "`miLLM` and `millm` and millm",
    ):
        read, identifier = li.classified_hits(text, export, DENY)
        assert sorted(read + identifier) == sorted(li.unallowed_hits(text, export, DENY)), text


OLD_IN_CLUSTER = {
    "millm-backend.millm.svc.cluster.local": "miserver-backend.miserver.svc.cluster.local",
    "mistudio-backend.mistudio.svc.cluster.local": "miworkbench-backend.miworkbench.svc.cluster.local",
}


def _with_hosts(status: str) -> li.Export:
    """The committed export with the two old in-cluster hosts recorded under ``status`` and under no
    other: a record the export already holds for one of them is REPLACED, so the "retired" variant
    never keeps a committed ``transition`` record beside it (the register lists both from P4)."""
    export = copy.deepcopy(li.load())
    export.records = [
        r for r in export.records if not (r["section"] == "host" and r["old"] in OLD_IN_CLUSTER)
    ]
    for old, new in OLD_IN_CLUSTER.items():
        record = {"section": "host", "old": old, "new": new, "status": status, "observed": True}
        li.validate_record(record, 0)
        export.records.append(record)
    assert [r["status"] for r in export.section("host") if r["old"] in OLD_IN_CLUSTER] == [
        status
    ] * 2
    return export


def test_the_listed_old_in_cluster_hosts_are_allowed_as_whole_names() -> None:
    """From P4 the COMMITTED export lists the siblings' two old in-cluster services (the
    previous-address lists of ``k8s/base/config.yaml`` name them until P7). Each is allowed as a
    whole host name, in a literal and in a manifest's comma list, and an address beside it that no
    record lists is still read. Not a row of ``scope_cases.json``: that table must give the same
    answers on the mirror, whose public export withholds these records (skipped there BY NAME)."""
    export = li.load()
    for old in OLD_IN_CLUSTER:
        record = li.require_host(export, old)
        assert record["status"] == "transition", record
        url = f"http://{old}:8000"
        assert li.unallowed_hits(url, export, DENY) == []
        assert li.classified_hits(url, export, DENY, whole=True, code_spans=True) == ([], [])
        both = f"{url},http://millm-backend.millm.example:8000"
        assert li.classified_hits(both, export, DENY, whole=False, code_spans=False) == (
            ["millm", "millm"],
            [],
        )


def test_host_allowances_come_only_from_transition_records() -> None:
    """A ``host`` record in transition allows its old address (the previous-address lists name the
    old in-cluster services until P7); a retired one allows nothing; no record, no allowance.
    """
    line = "http://millm-backend.millm.svc.cluster.local:8000"
    assert li.unallowed_hits(line, _with_hosts("transition"), DENY) == []
    assert li.unallowed_hits(line, _with_hosts("retired"), DENY) == ["millm", "millm"]
    without = copy.deepcopy(li.load())
    without.records = [r for r in without.records if r["section"] != "host"]
    assert li.unallowed_hits(line, without, DENY) == ["millm", "millm"]
    # The longer name is not allowed by a shorter record's prefix alone.
    assert li.unallowed_hits("millm-backend.millm.example", _with_hosts("transition"), DENY) != []
    # The variants differ only in those two records: nothing else of the export was dropped.
    kept = [r for r in li.load().records if r.get("old") not in OLD_IN_CLUSTER]
    for status in ("transition", "retired"):
        assert [
            r for r in _with_hosts(status).records if r.get("old") not in OLD_IN_CLUSTER
        ] == kept

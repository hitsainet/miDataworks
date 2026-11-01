"""The reply grammar is the output barrier (FTASKS 4.5). Pure: needs no bundle."""

from __future__ import annotations

from src.runner.reply import reduce
from src.runner_contract.reasons import Reason

CAP = 1000


def r(raw: bytes, mode: str = "run"):
    return reduce(raw, mode=mode, output_limit=4096, count_cap=CAP)


def test_a_plain_count() -> None:
    assert r(b'{"phase":"call","result":{"t":"int","v":"5"}}\n').count == 5


def test_overflow_and_cap_clamp() -> None:
    assert r(b'{"phase":"call","result":{"t":"int","v":"overflow"}}\n').count == CAP
    assert r(b'{"phase":"call","result":{"t":"int","v":"999999"}}\n').count == CAP


def test_negative_and_float_and_bool_are_invalid() -> None:
    assert (
        r(b'{"phase":"call","result":{"t":"int","v":"negative"}}\n').reason
        is Reason.RULE_OUTPUT_INVALID
    )
    assert r(b'{"phase":"call","result":{"t":"float"}}\n').reason is Reason.RULE_OUTPUT_INVALID
    assert r(b'{"phase":"call","result":{"t":"bool"}}\n').reason is Reason.RULE_OUTPUT_INVALID


def test_two_lines_is_invalid() -> None:
    assert (
        r(b'{"phase":"call","result":{"t":"int","v":"1"}}\n{"x":1}\n').reason
        is Reason.RULE_OUTPUT_INVALID
    )


def test_no_trailing_newline_is_invalid() -> None:
    assert r(b'{"phase":"call","result":{"t":"int","v":"1"}}').reason is Reason.RULE_OUTPUT_INVALID


def test_a_valid_object_with_a_trailing_blank_line_is_invalid() -> None:
    # Pins the explicit one-line count check: json.loads tolerates trailing whitespace, so without
    # it a forged blank line after a valid count would be accepted (mutation control M7/19).
    assert (
        r(b'{"phase":"call","result":{"t":"int","v":"1"}}\n\n').reason is Reason.RULE_OUTPUT_INVALID
    )


def test_over_the_output_limit_is_invalid() -> None:
    big = b'{"phase":"call","result":{"t":"int","v":"1"}}' + b" " * 5000 + b"\n"
    assert (
        reduce(big, mode="run", output_limit=4096, count_cap=CAP).reason
        is Reason.RULE_OUTPUT_INVALID
    )


def test_load_refused_carries_the_name() -> None:
    got = r(b'{"phase":"load","result":{"fail":"load_refused","refused":"os"}}\n')
    assert got.reason is Reason.RULE_LOAD_REFUSED and got.refused == "os"


def test_error_diag_is_parsed() -> None:
    got = r(
        b'{"phase":"call","result":{"fail":"error","diag":{"exception_type":"ValueError","line":2,"message":"x"}}}\n'
    )
    assert got.reason is Reason.RULE_ERROR and got.diag == {
        "exception_type": "ValueError",
        "line": 2,
        "message": "x",
    }


def test_memory_and_recursion_map_to_resource_limit() -> None:
    assert r(b'{"phase":"call","result":{"fail":"memory"}}\n').reason is Reason.RULE_RESOURCE_LIMIT
    assert (
        r(b'{"phase":"load","result":{"fail":"recursion"}}\n').reason is Reason.RULE_RESOURCE_LIMIT
    )


def test_inspect_grammar() -> None:
    got = r(
        b'{"phase":"load","inspect":{"functions":["f","g"],"imports":["re"]}}\n',
        mode="inspect",
    )
    assert got.inspect == {"functions": ["f", "g"], "imports": ["re"]}


def test_garbage_is_invalid() -> None:
    assert r(b"not json\n").reason is Reason.RULE_OUTPUT_INVALID
    assert r(b"").reason is Reason.RULE_OUTPUT_INVALID

"""016's rule-set schema already carries the code_rule signal's function and params (FR-018.16; FTASKS 3.11).

018 adds no field to ``midataworks-rule-set-v1.json`` (X-10): the ``code_rule`` signal published by
016 must already declare ``function`` and ``params``. Read from the published file, not a list.
"""

from __future__ import annotations

import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
RULE_SET_SCHEMA = BACKEND / "src" / "schemas" / "data" / "midataworks-rule-set-v1.json"


def _code_rule_def(schema: dict) -> dict:
    for key, value in schema.get("$defs", {}).items():
        if (
            "CodeRule" in key
            and value.get("properties", {}).get("kind", {}).get("const") == "code_rule"
        ):
            return value
    raise AssertionError("no code_rule signal definition in the rule-set schema")


def test_the_code_rule_signal_declares_function_and_params() -> None:
    schema = json.loads(RULE_SET_SCHEMA.read_text(encoding="utf-8"))
    code_rule = _code_rule_def(schema)
    props = code_rule["properties"]
    assert "function" in props, "016's code_rule signal must carry function"
    assert "params" in props, "016's code_rule signal must carry params"
    assert "code_sha256" in props and "runner_contract" in props

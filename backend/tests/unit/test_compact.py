"""Summary views of large read results (DLP run G9, 2026-10-21)."""

from __future__ import annotations

from src.core.compact import compact, summarise_audit


def test_compact_cuts_lists_and_objects_and_says_so() -> None:
    out = compact({"a": list(range(30)), "b": {str(i): i for i in range(20)}}, items=5)
    assert out["a"][:5] == [0, 1, 2, 3, 4]
    assert out["a"][5] == {"_omitted": 25, "ask": "detail=full"}
    assert len(out["b"]) == 6 and out["b"]["_omitted"]["keys"] == 15


def test_an_audit_summary_keeps_every_columns_verdict() -> None:
    columns = [
        {
            "column": f"h{i}",
            "figure": i / 100,
            "control_mean": 0.5,
            "valid": True,
            "n_values": 8000,
            "per_value": [{"value": str(v), "n": 1} for v in range(8000)],
            "bins": list(range(100)),
        }
        for i in range(75)
    ]
    result = {"label_column": "label", "columns": columns, "chance": 0.5}
    out = summarise_audit(result)
    assert out is not None
    assert len(out["columns"]) == 75, "every column is kept"
    assert out["columns"][0]["column"] == "h74", "highest figure first"
    assert all("per_value" not in c and "bins" not in c for c in out["columns"])
    assert all(c["omitted"] == ["per_value", "bins"] for c in out["columns"])
    assert {c["column"]: c["figure"] for c in out["columns"]} == {
        c["column"]: c["figure"] for c in columns
    }

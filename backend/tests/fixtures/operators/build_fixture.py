"""Build ``contract_fixture.parquet`` for the Data-Juicer contract tests (FTDD 003 section 10.3).

Designed rows, not sampled ones: lengths on both sides of every offered filter's default band,
exact and near duplicates, non-English text, whitespace-only text, repeated n-grams, special
characters and a chat transcript. Every offered operator must both KEEP and DROP rows here, and
its statistic must differ across rows, so the fixture cannot agree with the code by construction.

Row keys are feature 002's real keys over ``text``. Regenerate only deliberately:

    cd backend && .venv/bin/python -m tests.fixtures.operators.build_fixture
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.services.row_keys import compute_row_key

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "contract_fixture.parquet"

TEXTS = [
    "Why did the chicken cross the road? To get to the other side, of course.",
    "Why did the chicken cross the road? To get to the other side, of course.",
    "Why did the chicken cross the road? To get to the other side of course!",
    "ok",
    "   ",
    "Quarterly earnings beat expectations as revenue rose for the third straight quarter.",
    "La réunion du comité a eu lieu mardi et les décisions seront publiées demain matin.",
    "会议于星期二举行，决定将于明天上午公布。这是一个较长的中文句子用于测试。",
    "buy now buy now buy now buy now buy now buy now buy now buy now buy now",
    "!!!! $$$$ #### @@@@ %%%% ^^^^ &&&& **** (((( )))) !!!! $$$$ ####",
    "user: tell me a joke\nassistant: I told my wife she was drawing her eyebrows too high. "
    "She looked surprised.",
    "Time flies like an arrow; fruit flies like a banana.",
    "A pun is its own reword.",
    "short line",
    "The committee met on Tuesday to discuss the budget, the schedule and the hiring plan "
    "for the next two quarters, and agreed to publish the minutes by Friday afternoon.",
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
]


def build() -> pa.Table:
    rows = []
    seen: dict[str, int] = {}
    for index, text in enumerate(TEXTS):
        key = compute_row_key({"text": text}, ["text"])
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        rows.append(
            {
                "text": text,
                "note": f"row {index}",
                "_dw_row_key": key,
                "_dw_occurrence": occurrence,
                "_dw_split": "train",
            }
        )
    schema = pa.schema(
        [
            ("text", pa.string()),
            ("note", pa.string()),
            ("_dw_row_key", pa.string()),
            ("_dw_occurrence", pa.int32()),
            ("_dw_split", pa.string()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


if __name__ == "__main__":
    pq.write_table(build(), OUTPUT)
    print(f"wrote {OUTPUT}")

"""Heuristic filters (FR-004.16; FTDD 004 §6.2): length band, turn-count band, empty content and a
repeated word n-gram ratio.

Each declares its threshold in the manifest and implements ``compute_statistics`` (FR-004.4), so
003's threshold control draws it with no operator-specific UI. Character-ratio and special-character
filters come from Data-Juicer's allowlisted catalogue (``dj_alphanumeric_filter``,
``dj_special_characters_filter``, ``dj_character_repetition_filter``); the word n-gram repetition
filter is native because the catalogue's repetition filter counts characters. Language
identification has no allowlisted operator and no approved library, so it is not offered
(``docs/curation.md``) and the profile reports it ``not_computed``.

One module for the four filters rather than one file each (FTID §2): they share a statistic
pattern and nothing else; the operator NAMES are what recipes record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pyarrow as pa

from ....services.curation import text_stats
from ...context import RunContext
from ...manifest import ThresholdSpec
from ...protocol import OperatorResult
from .common import manifest, param, refuse

Stat = Callable[[Any], float]


def _column(batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> str:
    column = params.get("column")
    if column is None:
        content = ctx.content_columns
        if len(content) != 1:
            raise refuse(
                "params_invalid",
                f"Name the column to measure; this version has {len(content)} content columns.",
                {"content_columns": content},
            )
        column = content[0]
    if column not in batch.schema.names:
        raise refuse("params_invalid", f"The input has no column {column!r}.", {"column": column})
    return str(column)


def _band_filter(
    batch: pa.Table,
    ctx: RunContext,
    stat: str,
    values: list[float],
    low: float | None,
    high: float | None,
    reason_code: str,
    unit: str,
) -> OperatorResult:
    keys = batch.column("_dw_row_key").to_pylist()
    occ = batch.column("_dw_occurrence").to_pylist()
    keep, events = [], []
    for i, value in enumerate(values):
        if low is not None and value < low:
            events.append(
                ctx.drop(
                    (keys[i], occ[i]),
                    reason_code,
                    f"{stat} {value:g} {unit} is below " f"{low:g}",
                    stat,
                    value,
                    low,
                    "<",
                )
            )
        elif high is not None and value > high:
            events.append(
                ctx.drop(
                    (keys[i], occ[i]),
                    reason_code,
                    f"{stat} {value:g} {unit} is above " f"{high:g}",
                    stat,
                    value,
                    high,
                    ">",
                )
            )
        else:
            keep.append(i)
    return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)


class LengthBand:
    manifest = manifest(
        "length_band",
        "filter",
        "Drops rows whose content is shorter or longer than a band, in characters or words.",
        params={
            "column": {"type": "string", "minLength": 1, "title": "Column"},
            "unit": {"type": "string", "enum": ["characters", "words"], "default": "characters"},
            "min_length": {
                "type": "number",
                "minimum": 0,
                "default": 0,
                "title": "Minimum",
                "x-widget": "slider",
            },
            "max_length": {
                "type": "number",
                "minimum": 0,
                "default": 100000,
                "title": "Maximum",
                "x-widget": "slider",
            },
        },
        thresholds=(
            ThresholdSpec(
                param="min_length",
                statistic="length",
                unit="characters or words",
                drop_when="below",
                pair_param="max_length",
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        column = _column(batch, params, ctx)
        fn = (
            text_stats.word_length
            if param(params, "unit", "characters") == "words"
            else (text_stats.char_length)
        )
        return {"length": pa.array([float(fn(v)) for v in batch.column(column).to_pylist()])}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        low, high = float(param(params, "min_length", 0)), float(param(params, "max_length", 1e5))
        if low > high:
            raise refuse(
                "params_invalid",
                "The band's minimum is above its maximum.",
                {"min_length": low, "max_length": high},
            )
        values = self.compute_statistics(batch, params, ctx)["length"].to_pylist()
        return _band_filter(
            batch,
            ctx,
            "length",
            values,
            low,
            high,
            "length_out_of_band",
            str(param(params, "unit", "characters")),
        )


class TurnCountBand:
    manifest = manifest(
        "turn_count_band",
        "filter",
        "Drops chat rows with fewer or more turns than a band.",
        params={
            "column": {"type": "string", "minLength": 1, "default": "messages", "title": "Column"},
            "min_turns": {"type": "integer", "minimum": 0, "default": 1, "x-widget": "slider"},
            "max_turns": {"type": "integer", "minimum": 0, "default": 64, "x-widget": "slider"},
        },
        thresholds=(
            ThresholdSpec(
                param="min_turns",
                statistic="turns",
                unit="turns",
                drop_when="below",
                pair_param="max_turns",
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        column = str(param(params, "column", "messages"))
        if column not in batch.schema.names:
            raise refuse("params_invalid", f"The input has no column {column!r}.")
        counts = [text_stats.turns(v) for v in batch.column(column).to_pylist()]
        return {"turns": pa.array([float(c) if c is not None else 0.0 for c in counts])}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        values = self.compute_statistics(batch, params, ctx)["turns"].to_pylist()
        return _band_filter(
            batch,
            ctx,
            "turns",
            values,
            float(param(params, "min_turns", 1)),
            float(param(params, "max_turns", 64)),
            "turns_out_of_band",
            "turns",
        )


class EmptyContent:
    manifest = manifest(
        "empty_content",
        "filter",
        "Drops rows whose content columns are all empty or whitespace.",
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        content = [c for c in ctx.content_columns if c in batch.schema.names]
        columns = [batch.column(c).to_pylist() for c in content]
        keys = batch.column("_dw_row_key").to_pylist()
        occ = batch.column("_dw_occurrence").to_pylist()
        keep, events = [], []
        for i in range(batch.num_rows):
            chars = sum(len(text_stats.as_text(col[i]).strip()) for col in columns)
            if chars == 0:
                events.append(
                    ctx.drop(
                        (keys[i], occ[i]),
                        "empty_content",
                        "every content column is empty or whitespace",
                        "content_chars",
                        0.0,
                    )
                )
            else:
                keep.append(i)
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)


class NgramRepetition:
    manifest = manifest(
        "ngram_repetition",
        "filter",
        "Drops rows where too large a share of word n-grams repeat.",
        params={
            "column": {"type": "string", "minLength": 1, "title": "Column"},
            "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
            "max_ratio": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0.5,
                "x-widget": "slider",
            },
        },
        thresholds=(
            ThresholdSpec(
                param="max_ratio", statistic="repeated_ngram_ratio", unit="share", drop_when="above"
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        column = _column(batch, params, ctx)
        n = int(param(params, "n", 3))
        return {
            "repeated_ngram_ratio": pa.array(
                [text_stats.repeated_ngram_ratio(v, n) for v in batch.column(column).to_pylist()]
            )
        }

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        values = self.compute_statistics(batch, params, ctx)["repeated_ngram_ratio"].to_pylist()
        return _band_filter(
            batch,
            ctx,
            "repeated_ngram_ratio",
            values,
            None,
            float(param(params, "max_ratio", 0.5)),
            "ngram_repetition",
            "share",
        )


FILTERS: tuple[type, ...] = (LengthBand, TurnCountBand, EmptyContent, NgramRepetition)

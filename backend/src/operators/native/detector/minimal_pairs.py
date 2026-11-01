"""The minimal-pair operators (009 FR-009.60 - FR-009.64; operator decision 2026-10-07).

The spec's single ``minimal_pair_generator@1`` could not work: it needed two endpoint roles while
003's ``RunContext.endpoint`` resolves one, and its judge check is a 005 label run, which an
operator (no database session) cannot write. So the generator is a CHAIN of pieces that each record
their own provenance (``services/detector_sets/minimal_pair_chain.py``):

1. a 007 generation run in ``minimal_pairs`` mode writes one minimal edit per seed row;
2. ``dw_generated_rows@1`` + :class:`MinimalPairScope` build the version the judge reads: the run's
   counterparts and their seeds, nothing else, each counterpart within the edit cap;
3. a 005 judge label run with a pinned rubric labels that version;
4. :class:`MinimalPairJoin` keeps only verified flips, assigns ``pair_id`` and records the
   provenance (FR-009.63).

Both operators read only RECORDED runs through bindings (``runs/<id>/gen/committed.json`` and
``runs/<id>/labels.parquet`` + ``labeler.json``), as 005's threshold labeler and 009's hard-negative
miner do, so a build never calls a model (002 FR-002.2). The rules they apply live in
``services/detector_sets/minimal_pairs.py``; nothing here re-decides them.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....core.storage import resolve_under_data_dir
from ....services.detector_sets import minimal_pairs as mp
from ....services.generation import rules as generation_rules
from ...context import RunContext
from ...errors import OperatorError
from ...manifest import ColumnSpec, OperatorManifest, ResourceSpec
from ...protocol import OperatorResult
from ..curation.common import key_order, pairs, read_all, schema
from ..generation import committed_manifest, committed_records, seed_splits
from .common import published_labels

PROVIDER_VERSION = "009-1"
MODE = "minimal_pairs"


def require_bound(ctx: RunContext, kind: str, run_id: str) -> None:
    """A build reads only a run it BINDS (002 FR-002.2). Fail closed in a build: an empty binding
    list is not a licence. Only a preview (no step execution) may read an unbound run."""
    if ctx.step_execution_id is None:
        return
    if run_id not in {str(b.get("id")) for b in ctx.bindings if b.get("kind") == kind}:
        raise OperatorError(
            f"{kind}_not_bound",
            f"{ctx.manifest.ref_text} reads {kind} {run_id}, which this build does not bind.",
            {"kind": kind, "id": run_id},
        )


def minimal_pair_manifest(run_id: str) -> dict[str, Any]:
    manifest = committed_manifest(run_id)
    if manifest.get("mode") != MODE or not manifest.get("prompt_column"):
        raise OperatorError(
            "not_a_minimal_pair_run",
            f"Generation run {run_id} is a {manifest.get('mode')} run; minimal pairs read a "
            "minimal_pairs run.",
            {"generation_run_id": run_id, "mode": manifest.get("mode")},
        )
    return manifest


def counterpart_records(manifest: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """record index -> the run's committed, GENERATED respond record."""
    return {
        int(r["record_index"]): r
        for r in committed_records(manifest, "respond")
        if r["outcome"] == "generated"
    }


def record_for(
    records: Mapping[int, dict[str, Any]], row: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The committed record a generated row came from (its ``generation_record_index``)."""
    index = row.get("generation_record_index")
    return None if index is None else records.get(int(index))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


# --- step 2: the version the judge reads ----------------------------------------------------

SCOPE_PARAMS: dict[str, Any] = {
    "generation_run_id": {"type": "string", "minLength": 1, "title": "Minimal-pair run"},
    "max_edit_chars": {
        "type": "integer",
        "minimum": 1,
        "title": "Largest edit (characters)",
        "x-unit": "characters",
    },
    "max_edit_words": {
        "type": "integer",
        "minimum": 1,
        "title": "Largest edit (words)",
        "x-unit": "words",
    },
}


class MinimalPairScope:
    """Keeps a minimal-pair run's counterparts and their seed rows, and nothing else.

    A counterpart that changed nothing (``no_edit``) or more than the cap (``edit_too_large``,
    statistic = the edit size) is dropped here, so the judge never reads it; a seed whose
    counterpart went goes too (``seed_without_counterpart``). Every other row is dropped as
    ``not_in_run``: the judge labels exactly the pairs, not the whole version.
    """

    manifest = OperatorManifest(
        name="minimal_pair_scope",
        version="1",
        provider="native",
        provider_version=PROVIDER_VERSION,
        kind="selector",
        scope="dataset",
        description=(
            "Keeps a minimal-pair generation run's counterparts within the edit cap and their seed "
            "rows, so a judge reads only the pairs."
        ),
        params_schema=schema(SCOPE_PARAMS, ("generation_run_id",)),
        resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="medium"),
        deterministic=True,
        binding_kinds=("generation_run",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["generation_run_id"])
        require_bound(ctx, "generation_run", run_id)
        manifest = minimal_pair_manifest(run_id)
        column = str(manifest["prompt_column"])
        records = counterpart_records(manifest)
        all_seeds = set(seed_splits(manifest))
        max_chars, max_words = params.get("max_edit_chars"), params.get("max_edit_words")
        table = key_order(read_all(ctx))
        if column not in table.schema.names:
            raise OperatorError(
                "target_columns_missing", f"The input has no column {column!r}.", {"column": column}
            )
        rows = table.to_pylist()
        ids = pairs(table)
        seed_at: dict[str, int] = {}
        for i, row in enumerate(rows):
            if row.get("_dw_origin") == "source":
                seed_at.setdefault(str(row["_dw_row_key"]), i)
        keep: set[int] = set()
        events = []
        counts: Counter[str] = Counter()
        sizes: list[dict[str, Any]] = []
        handled: set[int] = set()
        seeds_seen: set[str] = set()
        for i, row in enumerate(rows):
            if row.get("_dw_origin") != "generated" or row.get("generation_run_id") != run_id:
                continue
            handled.add(i)
            record = record_for(records, row)
            seed_key = None if record is None else str(record["seed_row_key"])
            if seed_key is None or seed_key not in seed_at:
                counts["seed_missing"] += 1
                events.append(
                    ctx.drop(
                        ids[i],
                        "seed_missing",
                        "its seed row is not in the input",
                        "pair_membership",
                        text="seed missing",
                    )
                )
                continue
            if seed_key in seeds_seen:
                raise OperatorError(
                    "several_counterparts",
                    f"Seed {seed_key[:12]} has more than one counterpart; a minimal-pair run "
                    "writes one per seed.",
                    {"seed_row_key": seed_key},
                )
            seeds_seen.add(seed_key)
            size = mp.edit_size(_text(rows[seed_at[seed_key]][column]), _text(row[column]))
            text = f"chars={size.chars} words={size.words}"
            if size.chars == 0:
                counts["no_edit"] += 1
                events.append(
                    ctx.drop(
                        ids[i],
                        "no_edit",
                        "the counterpart is the seed unchanged",
                        "edit_chars",
                        0,
                        text=text,
                    )
                )
                continue
            over = mp.over_cap(size, max_chars, max_words)
            if over is not None:
                counts["edit_too_large"] += 1
                cap = max_chars if over == "chars" else max_words
                value = size.chars if over == "chars" else size.words
                events.append(
                    ctx.drop(
                        ids[i],
                        "edit_too_large",
                        f"the edit changes {value} {over}, over the cap of {cap}",
                        f"edit_{over}",
                        value,
                        cap,
                        "above",
                        text=text,
                    )
                )
                continue
            keep.update((i, seed_at[seed_key]))
            sizes.append({"seed_row_key": seed_key, **size.as_dict()})
        for i, row in enumerate(rows):
            if i in keep or i in handled:
                continue
            key = str(row["_dw_row_key"])
            code, why = (
                ("seed_without_counterpart", "its counterpart was dropped or never generated")
                if row.get("_dw_origin") == "source" and key in all_seeds
                else ("not_in_run", f"not a seed or counterpart of minimal-pair run {run_id}")
            )
            counts[code] += 1
            events.append(
                ctx.drop(ids[i], code, why, "pair_membership", text=code.replace("_", " "))
            )
        kept = sorted(keep)
        return OperatorResult(
            output=table.take(pa.array(kept, pa.int64())),
            events=events,
            report={
                "generation_run_id": run_id,
                "column": column,
                "pairs_in_scope": len(sizes),
                "max_edit_chars": max_chars,
                "max_edit_words": max_words,
                "dropped": dict(sorted(counts.items())),
                "edit_sizes": sizes,
            },
        )


# --- step 4: verified flips only, with their provenance --------------------------------------

JOIN_PARAMS: dict[str, Any] = {
    "generation_run_id": {"type": "string", "minLength": 1, "title": "Minimal-pair run"},
    "judge_label_run_id": {"type": "string", "minLength": 1, "title": "Judge run"},
    "flip_from": {"type": "string", "minLength": 1, "title": "The seed's verdict"},
    "flip_to": {"type": "string", "minLength": 1, "title": "The counterpart's verdict"},
}

#: (column, Arrow type) the join adds, in order (FR-009.60, FR-009.63).
PAIR_COLUMNS: tuple[tuple[str, pa.DataType], ...] = (
    ("pair_id", pa.string()),
    ("pair_role", pa.string()),
    ("pair_seed_row_key", pa.string()),
    ("pair_generation_run_id", pa.string()),
    ("pair_generator_model_id", pa.string()),
    ("pair_generator_revision", pa.string()),
    ("pair_steering_state", pa.string()),
    ("pair_steering_set_hash", pa.string()),
    ("pair_edit_chars", pa.int64()),
    ("pair_edit_words", pa.int64()),
    ("pair_judge_verdict", pa.string()),
    ("pair_judge_run_id", pa.string()),
    ("pair_judge_model_id", pa.string()),
    ("pair_judge_revision", pa.string()),
    ("pair_judge_rubric", pa.string()),
    ("pair_judge_identity_hash", pa.string()),
)


def judge_record(label_run_id: str) -> dict[str, Any]:
    """The judge's identity as 005 published it beside its labels (``labeler.json``)."""
    path = resolve_under_data_dir("runs", label_run_id, "labeler.json")
    if not path.is_file():
        raise OperatorError(
            "judge_identity_not_published",
            f"Label run {label_run_id} published no labeler identity; bind a completed judge run.",
            {"label_run_id": label_run_id},
        )
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if document.get("label_run_id") != label_run_id or document.get("kind") != "judge":
        raise OperatorError(
            "judge_run_required",
            f"Label run {label_run_id} is a {document.get('kind')} run; a minimal pair is verified "
            "by a judge run with a rubric.",
            {"label_run_id": label_run_id, "kind": document.get("kind")},
        )
    return document


class MinimalPairJoin:
    """Keeps a pair only when the judge verified the flip; writes ``pair_id`` and provenance.

    An unverified pair is dropped, BOTH rows, with the judge's verdict as the reason
    (``flip_not_verified``, ``seed_not_flip_from``, ``judge_missing``, ``judge_provisional``) and
    counted in the step report; it is never kept. Rows that are not a pair of the bound run are
    dropped as ``not_in_run``. The judge must not be the generator (T-35): the same rule 007 applies
    at its plan, its worker and 005's preflight is applied again here to the identities the two runs
    RECORDED, so a hand-written recipe cannot pair a model's output with its own verdicts.
    """

    manifest = OperatorManifest(
        name="minimal_pair_join",
        version="1",
        provider="native",
        provider_version=PROVIDER_VERSION,
        kind="labeler",
        scope="dataset",
        description=(
            "Keeps minimal pairs whose flip a judge run verified, gives both rows a pair ID, and "
            "records seed, generator, steering state, edit size and the judge."
        ),
        output_columns=tuple(
            ColumnSpec(name=name, type=str(typ), role="metadata") for name, typ in PAIR_COLUMNS
        ),
        params_schema=schema(
            JOIN_PARAMS, ("generation_run_id", "judge_label_run_id", "flip_from", "flip_to")
        ),
        resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="medium"),
        deterministic=True,
        binding_kinds=("generation_run", "label_run"),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["generation_run_id"])
        judge_id = str(params["judge_label_run_id"])
        flip_from, flip_to = str(params["flip_from"]), str(params["flip_to"])
        if flip_from == flip_to:
            raise OperatorError(
                "flip_invalid", "flip_from and flip_to must be different verdicts.", {}
            )
        require_bound(ctx, "generation_run", run_id)
        require_bound(ctx, "label_run", judge_id)
        manifest = minimal_pair_manifest(run_id)
        column = str(manifest["prompt_column"])
        records = counterpart_records(manifest)
        judge = judge_record(judge_id)
        identity = dict(judge.get("labeler_identity") or {})
        judge_revision = identity.get("model_revision")
        self._independent(identity, records.values())
        labels = published_labels(judge_id)
        table = key_order(read_all(ctx))
        rows = table.to_pylist()
        ids = pairs(table)
        seed_at: dict[str, int] = {}
        for i, row in enumerate(rows):
            if row.get("_dw_origin") == "source":
                seed_at.setdefault(str(row["_dw_row_key"]), i)
        values: dict[int, dict[str, Any]] = {}
        events = []
        dropped: Counter[str] = Counter()
        paired: set[int] = set()
        pairs_in = 0
        for i, row in enumerate(rows):
            if row.get("_dw_origin") != "generated" or row.get("generation_run_id") != run_id:
                continue
            record = record_for(records, row)
            seed_key = None if record is None else str(record["seed_row_key"])
            if record is None or seed_key not in seed_at:
                continue  # dropped below as not_in_run
            assert seed_key is not None
            s = seed_at[seed_key]
            if s in paired:
                raise OperatorError(
                    "several_counterparts",
                    f"Seed {seed_key[:12]} has more than one counterpart; a minimal-pair run "
                    "writes one per seed.",
                    {"seed_row_key": seed_key},
                )
            pairs_in += 1
            check = mp.verify_flip(
                self._verdict(labels, str(rows[s]["_dw_row_key"])),
                self._verdict(labels, str(row["_dw_row_key"])),
                flip_from,
                flip_to,
            )
            paired.update((i, s))
            if not check.verified:
                dropped[check.reason_code] += 1
                for j in (i, s):
                    verdict = labels.get(str(rows[j]["_dw_row_key"])) or {}
                    events.append(
                        ctx.drop(
                            ids[j],
                            check.reason_code,
                            check.reason,
                            "verdict",
                            text=str(verdict.get("outcome") or "missing"),
                        )
                    )
                continue
            size = mp.edit_size(_text(rows[s][column]), _text(row[column]))
            shared = {
                "pair_id": mp.pair_id(seed_key),
                "pair_seed_row_key": seed_key,
                "pair_generation_run_id": run_id,
                "pair_generator_model_id": record.get("model_id"),
                "pair_generator_revision": record.get("model_revision") or mp.NOT_REPORTED,
                "pair_steering_state": mp.steering_state(
                    record.get("reported_steering"), record.get("steering_check")
                ),
                "pair_steering_set_hash": record.get("requested_set_hash")
                or generation_rules.NO_STEERING,
                "pair_edit_chars": size.chars,
                "pair_edit_words": size.words,
                "pair_judge_run_id": judge_id,
                "pair_judge_model_id": identity.get("model_id"),
                "pair_judge_revision": judge_revision or mp.NOT_REPORTED,
                "pair_judge_rubric": identity.get("template"),
                "pair_judge_identity_hash": judge.get("labeler_identity_hash"),
            }
            values[s] = {**shared, "pair_role": "seed", "pair_judge_verdict": flip_from}
            values[i] = {**shared, "pair_role": "counterpart", "pair_judge_verdict": flip_to}
        for i in range(len(rows)):
            if i in paired:
                continue
            dropped["not_in_run"] += 1
            events.append(
                ctx.drop(
                    ids[i],
                    "not_in_run",
                    f"not a pair of minimal-pair run {run_id}",
                    "pair_membership",
                    text="not in run",
                )
            )
        kept = sorted(values)
        output = table.take(pa.array(kept, pa.int64()))
        for name, typ in PAIR_COLUMNS:
            output = output.append_column(name, pa.array([values[i][name] for i in kept], typ))
        verified = len(kept) // 2
        return OperatorResult(
            output=output,
            events=events,
            output_roles={name: "metadata" for name, _ in PAIR_COLUMNS},
            report={
                "generation_run_id": run_id,
                "judge_label_run_id": judge_id,
                "flip_from": flip_from,
                "flip_to": flip_to,
                "pairs_in": pairs_in,
                "pairs_verified": verified,
                "pairs_dropped": pairs_in - verified,
                "dropped": dict(sorted(dropped.items())),
                "judge": {
                    "model_id": identity.get("model_id"),
                    "revision": judge_revision or mp.NOT_REPORTED,
                    "rubric": identity.get("template"),
                    "identity_hash": judge.get("labeler_identity_hash"),
                },
            },
        )

    @staticmethod
    def _verdict(labels: Mapping[str, Mapping[str, Any]], key: str) -> mp.JudgeVerdict | None:
        label = labels.get(key)
        if label is None:
            return None
        return mp.JudgeVerdict(label.get("outcome"), bool(label.get("provisional")))

    @staticmethod
    def _independent(identity: Mapping[str, Any], records: Any) -> None:
        judge = generation_rules.generator_identity(
            str(identity.get("model_id")),
            (
                None
                if identity.get("model_revision") in (None, generation_rules.NOT_REPORTED)
                else str(identity["model_revision"])
            ),
            None,
        )
        generators = {
            generation_rules.generator_identity(
                str(r.get("model_id")), r.get("model_revision"), r.get("requested_set_hash")
            )
            for r in records
        }
        conflicts = generation_rules.judge_conflicts(judge, sorted(generators, key=repr))
        if conflicts:
            error = generation_rules.conflict_error(judge, conflicts, inherited_from=None)
            raise OperatorError("judge_is_generator", error.message, {"conflicts": conflicts})


OPERATORS: tuple[type, ...] = (MinimalPairScope, MinimalPairJoin)

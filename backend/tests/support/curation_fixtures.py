"""Run feature 004's operators through 003's real executor (FTID 004 §8: "Operators through 003's
``execute_in_process`` with a real RunContext, asserting events, kind effects and conservation").

``run_operator`` builds the registry the PRODUCTION way (``native_operators()`` minus nothing), so
an operator that is not registered cannot be run here either.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.core.storage import resolve_under_data_dir
from src.operators.executor import GenerationStageSpec, StepResult, execute_in_process
from src.operators.registry import OperatorRegistry
from src.services.row_keys import ROWKEY_V1
from src.services.step_contract import EVENTS_FILE, META_FILE, part_files


@dataclass
class Ran:
    output: pa.Table
    events: pa.Table
    meta: dict[str, Any]
    result: StepResult

    def events_by_reason(self, reason: str) -> list[dict[str, Any]]:
        return [e for e in self.events.to_pylist() if e["reason_code"] == reason]


def registry() -> OperatorRegistry:
    return OperatorRegistry.build(catalogues=(), entry_points=())


def run_operator(
    name: str,
    params: dict[str, Any],
    data: pa.Table,
    roles: dict[str, str],
    *,
    seed: int = 20261005,
    reg: OperatorRegistry | None = None,
    report_sink: list[dict[str, Any]] | None = None,
) -> Ran:
    reg = reg or registry()
    version = reg.current_version(name)
    assert version is not None, f"{name} is not in the live registry"
    entry = reg.entry(name, version)
    output_dir = f"staging/test-{uuid.uuid4().hex}"
    spec = GenerationStageSpec(
        job_id="job-test",
        operator=name,
        version=version,
        expected_manifest_hash=str(entry.manifest_hash),
        params=params,
        output_dir=output_dir,
        step_seed=seed,
        column_roles=dict(roles),
        rowkey_scheme=ROWKEY_V1,
        input_table=data,
    )
    if report_sink is not None:
        impl = reg.implementation(entry)
        original = impl.run

        def capture(batch: pa.Table, p: Any, ctx: Any) -> Any:
            out = original(batch, p, ctx)
            if out.report is not None:
                report_sink.append(out.report)
            return out

        impl.run = capture  # instance attribute: the class is untouched
        try:
            result = execute_in_process(spec, registry=reg)
        finally:
            del impl.run
    else:
        result = execute_in_process(spec, registry=reg)
    directory = resolve_under_data_dir(output_dir)
    parts = part_files(directory)
    output = pa.concat_tables([pq.read_table(p) for p in parts]) if parts else data.slice(0, 0)
    events = pq.read_table(directory / EVENTS_FILE)
    meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
    return Ran(output, events, meta, result)


# --- versions on disk, with optional operator steps ------------------------------------------


@dataclass
class StepFixture:
    operator: str
    version: str = "1"
    #: Output parts of the step (its rows), written to runs/<job>/steps/<id>/.
    output: pa.Table | None = None
    #: Events of the step, in 002's EVENT_SCHEMA order (missing fields are null).
    events: list[dict[str, Any]] | None = None


def make_version(
    db: Any,
    table: pa.Table,
    roles: dict[str, str],
    *,
    steps: list[StepFixture] | None = None,
    split_roles: dict[str, bool] | None = None,
    seed: int = 20261005,
    dataset: Any = None,
) -> Any:
    """A completed version whose split files hold ``table`` (one file per ``_dw_split`` value)."""
    import hashlib

    from src.core.storage import run_dir, version_dir
    from src.models.step_execution import StepExecution
    from src.models.version import VersionStep
    from src.services.step_contract import EVENT_SCHEMA
    from tests.support import db_factories as f

    version_id = str(uuid.uuid4())
    directory = version_dir(version_id)
    directory.mkdir(parents=True, exist_ok=True)
    splits = []
    names = (
        sorted(set(table.column("_dw_split").to_pylist()))
        if "_dw_split" in table.schema.names
        else ["train"]
    )
    for name in names:
        part = (
            table.filter(pa.array([s == name for s in table.column("_dw_split").to_pylist()]))
            if "_dw_split" in table.schema.names
            else table
        )
        path = directory / f"{name}.parquet"
        pq.write_table(part, path)
        splits.append(
            {
                "name": name,
                "held_out": bool((split_roles or {}).get(name, False)),
                "rows": part.num_rows,
                "bytes": path.stat().st_size,
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "logical_digest": "0" * 64,
                "path": f"versions/{version_id}/{name}.parquet",
            }
        )
    version = f.version(
        db,
        ds=dataset,
        id=version_id,
        column_roles=dict(roles),
        splits=splits,
        total_rows=table.num_rows,
        seed=seed,
    )
    job = f.job(db)
    for index, step in enumerate(steps or [], start=1):
        execution_id = str(uuid.uuid4())
        out_dir = run_dir(job.id) / "steps" / execution_id
        out_dir.mkdir(parents=True, exist_ok=True)
        if step.output is not None:
            pq.write_table(step.output, out_dir / "part-00000.parquet")
        rows = [{name: e.get(name) for name in EVENT_SCHEMA.names} for e in step.events or []]
        pq.write_table(pa.Table.from_pylist(rows, schema=EVENT_SCHEMA), out_dir / "events.parquet")
        db.add(
            StepExecution(
                id=execution_id,
                identity_digest=hashlib.sha256(execution_id.encode()).hexdigest(),
                kind="operator",
                operator_name=step.operator,
                operator_version=step.version,
                manifest_hash="0" * 64,
                state="completed",
                output_dir=f"runs/{job.id}/steps/{execution_id}",
                job_id=job.id,
            )
        )
        db.flush()
        db.add(
            VersionStep(
                version_id=version_id,
                step_index=index,
                step_execution_id=execution_id,
                reused=False,
            )
        )
    db.commit()
    return version


@dataclass
class StubRegistry:
    """002's port shape (``get``) answering only the operators a test names."""

    infos: dict[str, Any]

    def get(self, name: str, version: str) -> Any:
        from src.services.operator_port import OperatorRefusal

        if name not in self.infos:
            raise OperatorRefusal("operator_not_found", name)
        return self.infos[name]


def labeler_info(name: str, outputs: dict[str, str]) -> Any:
    from src.services.operator_port import OperatorInfo

    return OperatorInfo(
        name=name, version="1", manifest_hash="0" * 64, kind="labeler", output_columns=outputs
    )

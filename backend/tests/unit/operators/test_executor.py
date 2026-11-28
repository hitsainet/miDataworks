"""Executor: lease-aware steps and the generation stage (FR-003.18, FR-003.25; FTASKS 6.6, 15.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.storage import resolve_under_data_dir
from src.operators import endpoint_port, executor
from src.operators.endpoint_port import Lease, ResolvedEndpoint
from src.operators.errors import OperatorError
from src.services.step_contract import read_meta
from tests.support import operator_fixtures as fx


class FakeResolver:
    def __init__(self, model: str = "judge-model", is_millm: bool = True) -> None:
        self.calls: list[str] = []
        self.model = model
        self.is_millm = is_millm

    def resolve(self, role: str) -> ResolvedEndpoint:
        self.calls.append(role)
        return ResolvedEndpoint(role, "http://upstream/v1", self.model, "sk-real", self.is_millm)


class FakeLeases:
    def __init__(self, offer: bool = True) -> None:
        self.offer = offer
        self.log: list[tuple[str, Any]] = []

    def acquire(self, endpoint: ResolvedEndpoint) -> Lease | None:
        self.log.append(("acquire", endpoint.model))
        return Lease(endpoint.model or "?", "lease-1", "rev-9") if self.offer else None

    def renew(self, lease: Lease) -> None:
        self.log.append(("renew", lease.lease_id))

    def release(self, lease: Lease) -> None:
        self.log.append(("release", lease.lease_id))


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeResolver, FakeLeases]:
    resolver, leases = FakeResolver(), FakeLeases()
    monkeypatch.setattr(endpoint_port, "_resolver", resolver)
    monkeypatch.setattr(endpoint_port, "_leases", leases)
    return resolver, leases


def test_a_lease_step_acquires_renews_and_releases_once(
    data_dir: Path, ports: tuple[FakeResolver, FakeLeases]
) -> None:
    resolver, leases = ports
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_endpoint_probe", data=fx.table())
    result = executor.execute_in_process(spec, registry=reg)
    assert resolver.calls == ["judge"], "one resolution, reused by ctx.endpoint"
    assert [entry[0] for entry in leases.log] == ["acquire", "renew", "release"]
    assert leases.log[0] == ("acquire", "judge-model")
    assert result.pinned is True and result.model == "judge-model"
    meta = read_meta(resolve_under_data_dir(spec.output_dir))
    assert meta["pinned"] is True and meta["output_column_roles"]["judge_model"] == "metadata"


def test_an_endpoint_without_a_lease_runs_unpinned_and_says_so(
    data_dir: Path, ports: tuple[FakeResolver, FakeLeases]
) -> None:
    """P-13: allowed, recorded as unpinned."""
    _, leases = ports
    leases.offer = False
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_endpoint_probe", data=fx.table())
    result = executor.execute_in_process(spec, registry=reg)
    assert result.pinned is False
    assert read_meta(resolve_under_data_dir(spec.output_dir))["pinned"] is False
    assert [e[0] for e in leases.log] == ["acquire"]


def test_the_lease_is_released_when_the_step_fails(
    data_dir: Path, ports: tuple[FakeResolver, FakeLeases], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.operators.native import fixtures

    _, leases = ports

    def explode(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(fixtures.EndpointProbe, "run", explode)
    reg = fx.registry()
    with pytest.raises(RuntimeError):
        executor.execute_in_process(
            fx.stage_spec(reg, "fx_endpoint_probe", data=fx.table()), registry=reg
        )
    assert leases.log[-1] == ("release", "lease-1")


def test_an_unconfigured_role_refuses_naming_settings(data_dir: Path) -> None:
    """12.5: until 005 installs resolve_endpoint the refusal names the Settings field.

    The unconfigured resolver is installed here rather than assumed: any earlier test on the same
    worker that runs the app lifespan leaves 005's ``PortResolver`` installed process-wide.
    """
    from src.operators import endpoint_port

    previous = endpoint_port.install_resolver(endpoint_port.UnconfiguredResolver())
    reg = fx.registry()
    try:
        with pytest.raises(OperatorError) as exc:
            executor.execute_in_process(
                fx.stage_spec(reg, "fx_endpoint_probe", data=fx.table()), registry=reg
            )
    finally:
        endpoint_port.install_resolver(previous)
    assert exc.value.code == "endpoint_unconfigured"
    assert "Settings" in exc.value.message


# --- 15.1 the generation stage -----------------------------------------------------------------


def test_generation_stage_runs_with_the_same_checks_and_writes_no_step_row(
    data_dir: Path, clean_db: None
) -> None:
    from sqlalchemy import text

    from src.core.database import get_sync_engine

    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_generator_echo", data=fx.table(["seed one", "seed two"]))
    result = executor.execute_in_process(spec, registry=reg)
    assert (result.rows_in, result.rows_kept, result.rows_added) == (2, 2, 2)
    out = resolve_under_data_dir(spec.output_dir)
    assert read_meta(out)["rows_added"] == 2
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_step_executions")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM dw_jobs")).scalar_one() == 0


def test_generation_stage_with_a_bad_parameter_is_refused(data_dir: Path) -> None:
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": "abc"}, data=fx.table())
    with pytest.raises(OperatorError) as exc:
        executor.execute_in_process(spec, registry=reg)
    assert exc.value.code == "params_invalid"
    assert not resolve_under_data_dir(spec.output_dir).exists()


def test_generation_stage_conservation_still_applies(data_dir: Path) -> None:
    reg = fx.registry()
    with pytest.raises(OperatorError) as exc:
        executor.execute_in_process(
            fx.stage_spec(reg, "fx_lose_row", data=fx.table()), registry=reg
        )
    assert exc.value.code == "conservation_violated"


def test_generation_stage_hands_body_overrides_to_the_context(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.operators.native import fixtures

    seen: dict[str, Any] = {}
    original = fixtures.KeepAll.run

    def spy(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        seen["overrides"] = ctx.body_overrides
        ctx.relay_records.append({"row_key": "k", "status": 200})
        return original(self, batch, params, ctx)

    monkeypatch.setattr(fixtures.KeepAll, "run", spy)
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_keep_all", data=fx.table(), body_overrides={"profile": "p1"})
    result = executor.execute_in_process(spec, registry=reg)
    assert seen["overrides"] == {"profile": "p1"}
    assert result.relay_records == [{"row_key": "k", "status": 200}]


# --- 15.3 the split effect ----------------------------------------------------------------------


def test_split_operator_passes_the_effect_checker_and_meta_carries_split_roles(
    data_dir: Path,
) -> None:
    """FR-003.27: assign_split is a selector effect; split_roles reach meta.json; conservation
    holds by (row key, occurrence) with ONE key in two splits."""
    reg = fx.registry()
    data = fx.table(["same text", "same text", "other text", "third text"])
    keys = data.column("_dw_row_key").to_pylist()
    assert keys[0] == keys[1]
    spec = fx.stage_spec(reg, "fx_split_half", data=data)
    result = executor.execute_in_process(spec, registry=reg)
    meta = read_meta(resolve_under_data_dir(spec.output_dir))
    assert meta["split_roles"] == {"train": {"held_out": False}, "test": {"held_out": True}}
    assert result.rows_split_assigned == 2 and result.rows_kept == 4
    import pyarrow.parquet as pq

    from src.services.step_contract import part_files

    rows = pq.read_table(part_files(resolve_under_data_dir(spec.output_dir))[0]).to_pylist()
    same = {(r["_dw_occurrence"], r["_dw_split"]) for r in rows if r["_dw_row_key"] == keys[0]}
    assert same == {(0, "train"), (1, "test")}, "one key, two splits, both accounted for"

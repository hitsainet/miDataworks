"""006 fills 008's seam (P-02, S3-05): a VALID calibration record lets C-6 pass; a missing, invalid
or insufficient one keeps it amber, so a public push and a detector-set send stay refused; a
``fails`` verdict passes C-6 with a card note. C-5 follows the audit. Every lookup goes through
008's own code: ``check_inputs.assemble`` -> ``feature_seams`` -> 006's modules, with the labeler
identities read by ``labeling.identity`` from the real ``dw_label_runs`` rows.

The ``passes`` and ``fails`` records are computed by the real job. ``invalid`` and ``insufficient``
cannot be produced through the API on purpose (import refuses a one-class set; a check failure needs
a defective load), so those two records are stored directly — the shape the worker writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.calibration import CalibrationRecord, CalibrationVerdict
from src.models.version import Version
from src.services.publishing import checks, feature_seams
from src.services.publishing.check_inputs import assemble
from tests.support import db_factories
from tests.support.calibration_fixtures import (
    LABELS,
    MAPPING,
    QUESTION,
    Runner,
    humor_rows,
    humor_scores,
    make_run,
    make_version,
    row_key,
)


def outcomes(version_id: str) -> dict[str, checks.CheckOutcome]:
    with sync_session_factory()() as s:
        version = s.get(Version, version_id)
        assert version is not None
        assembled = assemble(
            s, version, label_column=None, visibility="public", token_scope="write"
        )
        result = checks.evaluate_checks(assembled.inputs)
    by: dict[str, checks.CheckOutcome] = {}
    for o in result:
        by.setdefault(o.check, o)
    by.update({o.check: o for o in result if o.check.startswith("N-")})
    return by


async def calibrated(
    client: httpx.AsyncClient,
    runner: Runner,
    data_dir: Path,
    target: float | None,
    mapping: dict[str, Any] | None = None,
    signal: float = 0.6,
) -> tuple[str, str]:
    """(record id, bound version id): a real record for a run, and a version binding that run."""
    rows = humor_rows(200)
    version = make_version(data_dir, rows)
    run = make_run(version, humor_scores(rows, signal=signal))
    if target is not None:
        await client.put(
            "/api/v1/calibration-targets", json={"question": QUESTION, "target": target}
        )
    set_id = (
        await client.post(
            "/api/v1/calibration-sets/import",
            json={
                "version_id": version,
                "question": QUESTION,
                "label_set": LABELS,
                "mapping": mapping or MAPPING,
            },
        )
    ).json()["id"]
    job = (
        await client.post(
            "/api/v1/calibration-records",
            json={"label_run_id": run.id, "calibration_set_id": set_id},
        )
    ).json()["job_id"]
    record_id = runner.run(job)["record_id"]
    published = make_version(
        data_dir,
        [{"text": r["text"]} for r in rows],
        bindings=[{"kind": "label_run", "id": run.id}],
    )
    return record_id, published


def store_verdict(record_id: str, verdict: str) -> None:
    """Copy a computed record with another verdict, as the worker would have stored it."""
    with sync_session_factory()() as s:
        old = s.get(CalibrationRecord, record_id)
        assert old is not None
        job = db_factories.job(s, kind="calibration_compute")
        new = CalibrationRecord(
            id=new_id("cr"),
            calibration_set_id=old.calibration_set_id,
            label_run_id=old.label_run_id,
            labeler_identity=old.labeler_identity,
            labeler_identity_hash=old.labeler_identity_hash,
            labeler_fingerprint=old.labeler_fingerprint,
            score_kind=old.score_kind,
            metrics=old.metrics if verdict != "insufficient" else {**old.metrics, "auroc": None},
            metrics_sha256=old.metrics_sha256,
            settings=old.settings,
            warnings=[],
            job_id=job.id,
            created_by="Test Operator",
            created_by_origin="operator",
        )
        s.add(new)
        s.flush()
        s.add(CalibrationVerdict(record_id=new.id, verdict=verdict, rule=None, numbers={}))
        s.commit()


async def test_no_record_keeps_c6_amber(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = [{"text": f"t{i}"} for i in range(20)]
    version = make_version(data_dir, rows)
    run = make_run(version, {row_key(r["text"]): 0.5 for r in rows})
    published = make_version(data_dir, rows, bindings=[{"kind": "label_run", "id": run.id}])
    c6 = outcomes(published)["C-6"]
    assert c6.outcome is checks.Outcome.AMBER
    assert "no calibration record" in c6.reason
    assert checks.push_allowed([c6], "public")[0] is False


async def test_a_passing_record_turns_c6_green(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    record_id, published = await calibrated(client, runner, data_dir, target=0.55)
    with sync_session_factory()() as s:
        verdict = s.get(CalibrationVerdict, record_id)
        assert verdict is not None and verdict.verdict == "passes"
    by = outcomes(published)
    assert by["C-6"].outcome is checks.Outcome.GREEN, by["C-6"]
    assert "N-failing_verdict" not in by


@pytest.mark.parametrize("verdict", ["invalid", "insufficient"])
async def test_invalid_and_insufficient_keep_c6_amber(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path, verdict: str
) -> None:
    record_id, published = await calibrated(client, runner, data_dir, target=0.55)
    store_verdict(record_id, verdict)  # newer: the status lookup returns it
    c6 = outcomes(published)["C-6"]
    assert c6.outcome is checks.Outcome.AMBER, c6
    assert f"verdict {verdict}" in c6.reason
    assert checks.push_allowed([c6], "public") == (False, [c6])


async def test_a_failing_record_passes_c6_with_a_card_note(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    no_ceiling = {k: v for k, v in MAPPING.items() if k != "ratings"}  # the target rule decides
    record_id, published = await calibrated(client, runner, data_dir, 0.99, no_ceiling, 0.25)
    with sync_session_factory()() as s:
        verdict = s.get(CalibrationVerdict, record_id)
        assert verdict is not None and verdict.verdict == "fails"
        assert verdict.rule == "operator_target"
    by = outcomes(published)
    assert by["C-6"].outcome is checks.Outcome.GREEN
    assert by["N-failing_verdict"].outcome is checks.Outcome.NOTE


async def test_c5_follows_the_audit_end_to_end(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    _, published = await calibrated(client, runner, data_dir, target=0.55)
    assert outcomes(published)["C-5"].outcome is checks.Outcome.AMBER  # no audit drawn
    status = (await client.post(f"/api/v1/versions/{published}/audit", json={"size": 50})).json()
    assert status["state"] == "in_progress"  # the version's bound run supplied the question
    assert outcomes(published)["C-5"].outcome is checks.Outcome.AMBER
    page = (await client.get(f"/api/v1/review-queues/{status['queue_id']}/items?limit=100")).json()
    for item in page["items"]:
        await client.post(
            f"/api/v1/review-items/{item['id']}/decisions", json={"decision": "accept"}
        )
    c5 = outcomes(published)["C-5"]
    assert c5.outcome is checks.Outcome.GREEN, c5
    with sync_session_factory()() as s:
        assert feature_seams.audit_status(published, session=s).state == "complete"


async def test_the_status_payload_fits_008s_calibration_ref(
    client: httpx.AsyncClient, operator_name: str, runner: Runner, data_dir: Path
) -> None:
    from src.schemas.dataset_version import CalibrationRef

    record_id, published = await calibrated(client, runner, data_dir, target=0.55)
    with sync_session_factory()() as s:
        version = s.get(Version, published)
        assert version is not None
        refs: list[dict[str, Any]] = assemble(
            s, version, label_column=None, visibility="public", token_scope="write"
        ).calibration
    assert len(refs) == 1
    ref = CalibrationRef.model_validate(refs[0])
    assert ref.record_id == record_id and ref.calibration_set is not None
    assert ref.calibration_set.rows_shipped is False


async def test_the_projection_applies_overrides_and_omits_flagged_rows(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """11.3: 008's projection, fed by 006's resolver through the seam, ships the operator's
    override and drops a flagged-unresolved row; an agent accept changes nothing (FR-008.27)."""
    import pyarrow as pa

    from src.services.publishing.projection import Counters, apply_effective_labels

    texts = [f"t{i}" for i in range(4)]
    version = make_version(data_dir, [{"text": t} for t in texts])
    keys = [row_key(t) for t in texts]
    run = make_run(version, dict(zip(keys, [0.9, 0.9, 0.1, 0.1], strict=True)))
    queue = (
        await client.post(
            "/api/v1/review-queues",
            json={"kind": "label_review", "label_run_id": run.id, "row_keys": keys},
        )
    ).json()
    items = {
        i["row_key"]: i["id"]
        for i in (await client.get(f"/api/v1/review-queues/{queue['id']}/items")).json()["items"]
    }
    decide = "/api/v1/review-items/{}/decisions"
    await client.post(
        decide.format(items[keys[0]]),
        json={"decision": "override", "override_label": "not_humorous", "reason": "flat"},
    )
    await client.post(decide.format(items[keys[1]]), json={"decision": "flag", "reason": "unsure"})
    await client.post(
        decide.format(items[keys[2]]),
        json={"decision": "accept"},
        headers={"X-Dataworks-Agent": "agent:dataworks-mcp"},
    )
    batch = pa.RecordBatch.from_pydict(
        {"_dw_row_key": keys, "label": ["humorous", "humorous", "not_humorous", "not_humorous"]}
    )
    resolved = feature_seams.resolve_effective_labels(version, None, None, keys)
    counters = Counters()
    out = apply_effective_labels(batch, "label", resolved, counters)
    assert out.column("_dw_row_key").to_pylist() == [keys[0], keys[2], keys[3]]
    assert out.column("label").to_pylist() == ["not_humorous", "not_humorous", "not_humorous"]
    assert counters.overrides_applied == 1 and counters.omitted_flagged_unresolved == 1

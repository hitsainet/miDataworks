"""The 2026-10-08 live findings of the probe reproduction gate, end to end, on REAL rows.

Production: version ``edea5d8d-…`` (``Arrrlex/models-under-pressure`` ``mental_health_balanced``
test, 540 rows), link ``rpl_6fa2ade0…``, run ``lr_0631bc94…``. The rows here are 14 of those 540,
verbatim (``tests/fixtures/models_under_pressure.py``): every chat is JSON TEXT, and four rows read
``"nan"`` — one row key, labelled twice each way.

1. The JSON text is parsed by ``chat_json_parser`` (the operator itself runs, its OUTPUT is the
   version), the link reads the parsed column as chats ("not verified equal", not "differs"), and
   the run and the gate send the real messages. Mapping the JSON text to ``text`` is refused.
2. The plan, the link and the gate count the same rows (14) and the same keys (11), and the key
   whose copies carry both classes is named on all three.
3. A failed gate is not offered again: the plan reads ``failed`` naming the run, a start and a
   resume are refused, and a deliberate retry carries its reason onto the run.
4. The preflight names its bar: a length band of the window, beside the window's own bar.
5. Links rank by check level before role.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.services.detector_sets import reproduction
from tests.fixtures import models_under_pressure as mup
from tests.fixtures.humor_pool import with_system_columns
from tests.integration.detector_sets.test_probe_verdict_runs import LLAMA, plan
from tests.integration.detector_sets.test_reproduction_links import (
    RUN,
    SOURCE,
    _link_row,
    gate_texts,
    labelled_version,
)
from tests.support import db_factories
from tests.support.curation_fixtures import run_operator
from tests.support.fake_millm import ORIGIN, probe_row
from tests.support.fake_mistudio import BASE, FIXTURES, FakeMiStudio
from tests.support.labeling_fixtures import Labeling, labeling  # noqa: F401
from tests.support.send_fixtures import API

PROBE = "pr_mental_health"
VIEW = "pmd_2f2623b29a40"  # production's view id for mental_health_balanced
WINDOW_BAR = 25.289772033691406
BAND_BAR = 20.42040252685547  # production's band 0: the number the live preflight showed
BANDS = {
    "all": [
        {"min_tokens": 0, "max_tokens": 100_000, "threshold": BAND_BAR, "threshold_source": "band"},
        {
            "min_tokens": 100_001,
            "max_tokens": None,
            "threshold": 37.749,
            "threshold_source": "band",
        },
    ]
}
MAPPING = {"high-stakes": "positive", "low-stakes": "negative"}
#: AUROC of :func:`score_of` over the 14 rows with every copy kept (35 + 10 + 2·0.5 of 49).
EXPECTED_AUROC = 47 / 49


def _key_text(messages: list[dict[str, str]]) -> str:
    return " ".join(m["content"] for m in messages)  # what the fake miLLM hands its scorer


def score_of() -> dict[str, float]:
    """High for high-stakes, low for low-stakes, 5.0 for the shared ``"nan"`` input."""
    out: dict[str, float] = {}
    pos = neg = 0
    for r in mup.mental_health_rows():
        messages = json.loads(r["inputs"])
        if messages[-1]["content"] == mup.NAN_TURN:
            out[_key_text(messages)] = 5.0
        elif r["labels"] == "high-stakes":
            out[_key_text(messages)] = 10.0 + pos
            pos += 1
        else:
            out[_key_text(messages)] = float(neg)
            neg += 1
    return out


def _write_version(data_dir: Path, table: pa.Table, roles: dict[str, str], warnings: Any) -> str:
    version_id = db_factories.uid()
    relative = f"versions/{version_id}/test.parquet"
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    with sync_session_factory()() as db:
        db_factories.version(
            db,
            id=version_id,
            splits=[
                {
                    "name": "test",
                    "path": relative,
                    "rows": table.num_rows,
                    "held_out": False,
                    "bytes": path.stat().st_size,
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "logical_digest": hashlib.sha256(b"live-findings").hexdigest(),
                }
            ],
            total_rows=table.num_rows,
            column_roles=roles,
            warnings=warnings,
        )
        db.commit()
    return version_id


DISAGREE = [
    {
        "code": "duplicate_metadata_disagrees",
        "message": "Copies of 1 row key(s) disagree on ['labels']. Labels apply to every copy; "
        "check whether the copies really are the same row.",
        "details": {"columns": {"labels": 1}},
    }
]
ROLES = {"inputs": "content", "labels": "metadata"}


def raw_version(data_dir: Path) -> str:
    """The version as production imported it: JSON text in ``inputs``."""
    rows = [{"inputs": r["inputs"], "labels": r["labels"]} for r in mup.mental_health_rows()]
    table = with_system_columns(rows, split="test", content=("inputs",))
    return _write_version(data_dir, table, ROLES, DISAGREE)


def parsed_version(data_dir: Path) -> str:
    """The same rows after ``chat_json_parser``: its real output is the version."""
    rows = [{"inputs": r["inputs"], "labels": r["labels"]} for r in mup.mental_health_rows()]
    table = with_system_columns(rows, split="test", content=("inputs",))
    ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, table, ROLES)
    return _write_version(data_dir, ran.output, ROLES, DISAGREE)


def studio_with_view(auroc: float, ci: tuple[float, float]) -> FakeMiStudio:
    """miStudio as production served it for the view: chats it decoded (``json_messages``)."""
    fake = FakeMiStudio()
    report = json.loads((FIXTURES / "report_pm_c99519a98e08.json").read_text())
    template = copy.deepcopy(report["evaluations"][0])
    template.update({"dataset_id": VIEW, "status": "completed", "n_positive": 7, "n_negative": 7})
    template["id"] = "pme_5b2860f491f3"
    template["metrics"] = {
        **template.get("metrics", {}),
        "auroc": auroc,
        "ci": {"low": ci[0], "high": ci[1]},
        "name": "models-under-pressure mental_health_balanced",
        "n_positive": 7,
        "n_negative": 7,
    }
    report["evaluations"].append(template)
    # Production's mental-health probe pm_f736aa73969d records the served render form (captured
    # 2026-10-08, ``report_pm_f736aa73969d_2026-10-08.json``); this report predates the field.
    served = json.loads((FIXTURES / "report_pm_f736aa73969d_2026-10-08.json").read_text())
    report["probe"]["render_form"] = served["probe"]["render_form"]
    report["probe"]["render_served"] = served["probe"]["render_served"]
    fake.reports[SOURCE] = report
    for v in json.loads((FIXTURES / "probe_datasets_2026-10-07.json").read_text()):
        fake.views[v["id"]] = v
    view = copy.deepcopy(next(iter(fake.views.values())))
    view.update(
        {
            "id": VIEW,
            "name": "models-under-pressure mental_health_balanced",
            "role": "eval",
            "distribution": "out_of_distribution",
            "input_column": "inputs",
            "label_column": "labels",
            "label_mapping": MAPPING,
            "keyword_filter": None,
            "counts": {
                "kinds": {"json_messages": 14},
                "positive": 7,
                "negative": 7,
                "excluded": 0,
                "filtered_out": 0,
                "unparseable": 0,
            },
        }
    )
    fake.views[VIEW] = view
    fake.run_rows[RUN] = json.loads((FIXTURES / f"run_{RUN}.json").read_text())
    return fake


@pytest.fixture
def mh(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> Labeling:  # noqa: F811
    labeling.millm.resident = dict(LLAMA)
    labeling.millm.probes = {
        PROBE: probe_row(
            PROBE,
            hf_id=LLAMA["repo_id"],
            threshold=WINDOW_BAR,
            mistudio_probe_id=SOURCE,
            length_bands=BANDS,
        )
    }
    scores = score_of()
    labeling.millm.probe_score = lambda text: scores.get(text, 1.0)
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    return labeling


def use_studio(monkeypatch: pytest.MonkeyPatch, fake: FakeMiStudio) -> None:
    from src.clients import mistudio_client

    monkeypatch.setattr(mistudio_client, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)


def probe_body(version_id: str, holds: str = "messages", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "input_version_id": version_id,
        "role": "probe",
        "probe": {"probe_id": PROBE, "window": "all"},
        "field_map": {holds: "inputs"},
    }
    out.update(over)
    return out


async def link(client: httpx.AsyncClient, version_id: str) -> dict[str, Any]:
    made = await client.post(
        f"{API}/reproduction-links",
        json={
            "mistudio_probe_id": SOURCE,
            "probe_dataset_id": VIEW,
            "version_id": version_id,
            "split": "test",
        },
    )
    assert made.status_code == 201, made.text
    return dict(made.json())


def score_requests(m: Labeling) -> list[dict[str, Any]]:
    return [r.body for r in m.millm.requests if r.path == "/api/probes/score"]


# --- finding 1 + 2 + 4: parsed chats, one count, a named bar -------------------------------------


async def test_parsed_chats_reproduce_with_one_count_and_the_conflicting_key_named(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_studio(monkeypatch, studio_with_view(EXPECTED_AUROC, (0.90, 0.99)))

    # The raw JSON text mapped as text is refused, naming the parser (finding 1).
    raw = raw_version(data_dir)
    refused = await plan(client, probe_body(raw, holds="text"))
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "INPUT_IS_JSON_CHAT" and "chat_json_parser" in error["message"]
    # ... and mapped as messages, refused too: it holds text, not a list.
    wrong = await plan(client, probe_body(raw, holds="messages"))
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "FIELD_MAP_INVALID"

    parsed = parsed_version(data_dir)
    made = await link(client, parsed)
    # Parsed messages against miStudio's decoded chats under the served form: equal by render rule,
    # never "differs" and never claimed verified by token ids.
    assert made["scoring_form"]["agreement"] == "equal_by_render_rule", made["scoring_form"]
    assert made["scoring_form"]["token_ids_compared"] is False
    assert made["scoring_form"]["millm"]["input_form"] == "messages"
    keys = made["checks"]["row_keys"]
    assert (keys["rows"], keys["row_keys"], keys["keys_with_copies"]) == (14, 11, 1)
    assert keys["conflicting"]["count"] == 1 and keys["conflicting"]["rows"] == 4
    assert keys["conflicting"]["keys"][0]["positive"] == 2
    assert keys["conflicting"]["keys"][0]["negative"] == 2
    assert made["checks"]["row_count"]["ours"] == 14  # miStudio counted 14: the link still holds

    planned = await plan(client, probe_body(parsed))
    assert planned.status_code == 200, planned.text
    p = planned.json()
    # The plan counts the same rows and keys as the link.
    assert p["rows_total"] == 11
    coverage = p["row_coverage"]
    assert (coverage["rows"], coverage["row_keys"], coverage["keys_with_copies"]) == (14, 11, 1)
    assert coverage["rows_in_copied_keys"] == 4
    assert coverage["copies_disagree"]["code"] == "duplicate_metadata_disagrees"
    gate = p["reproduction"]
    assert gate["state"] == "will_run" and gate["link_check_level"] == "counts_only"
    assert gate["row_keys"]["rows"] == 14 and gate["row_keys"]["row_keys"] == 11
    assert gate["row_keys"]["conflicting"]["count"] == 1
    assert gate["scoring_form"]["agreement"] == "equal_by_render_rule"
    assert p["labeler_identity"]["input_form"] == "messages"
    # Finding 4: the preflight's bar is named as the window's length band, beside the window bar.
    bar = p["probe"]["preflight"]["bar"]
    assert bar["kind"] == "length_band" and bar["threshold"] == pytest.approx(BAND_BAR)
    assert bar["window_threshold"] == pytest.approx(WINDOW_BAR)
    assert bar["label"] == "length band 0–100000 tokens: 20.42; window 'all' bar: 25.29"
    assert p["probe"]["preflight"]["threshold"] == pytest.approx(BAND_BAR)

    before = mh.millm.probe_score_calls
    started = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    assert started.status_code == 201, started.text
    assert started.json()["row_coverage"]["rows"] == 14
    final = mh.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    record = final.endpoint_snapshot["reproduction"]
    assert record["state"] == "passed"
    # Every copy counted, as miStudio counted it; each distinct input scored ONCE.
    assert record["rows_scored"] == 14 and record["rows_dropped"] == 0
    assert record["millm_auroc"] == pytest.approx(EXPECTED_AUROC, abs=1e-6)
    assert record["row_keys"]["row_keys"] == 11
    assert record["row_keys"]["conflicting"]["count"] == 1
    # The start's own plan preflight (1) + 11 gate requests + 11 label requests: no input twice.
    assert mh.millm.probe_score_calls - before == 23
    # The real messages went to miLLM: a system turn and the user's turn, never JSON characters.
    sent = score_requests(mh)[-1]["inputs"][0]
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert not sent["messages"][-1]["content"].startswith("[{")
    assert final.rows_total == 11 and final.row_coverage["rows"] == 14


def test_the_failure_message_states_the_count_and_the_conflict() -> None:
    record = {
        "rows_scored": 14,
        "view_name": "v",
        "role": "ood_eval",
        "millm_auroc": 0.96,
        "mistudio_auroc": 0.94,
        "mistudio_ci": [0.92, 0.95],
        "source": "linked_mistudio_evaluation",
        "link_id": "rpl_x",
        "link_check_level": "counts_only",
        "row_keys": {
            "rows": 14,
            "row_keys": 11,
            "keys_with_copies": 1,
            "conflicting": {
                "count": 1,
                "rows": 4,
                "keys": [{"row_key": "a" * 64, "positive": 2, "negative": 2}],
            },
        },
    }
    message = reproduction.failure_message(record)
    assert "14 rows, 11 distinct inputs" in message
    assert "BOTH positive and negative" in message and "2 positive, 2 negative" in message


# --- finding 3: a failed gate is not offered again ------------------------------------------------


async def test_a_failed_gate_reads_failed_refuses_start_and_resume_and_retries_with_a_reason(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # miStudio's interval excludes the 0.9592 miLLM reproduces: the gate fails.
    use_studio(monkeypatch, studio_with_view(0.55, (0.50, 0.60)))
    parsed = parsed_version(data_dir)
    made = await link(client, parsed)
    started = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    assert started.status_code == 201, started.text
    failed = mh.run_until_done(started.json()["id"])
    assert failed.state == "failed" and failed.error["code"] == "REPRODUCTION_FAILED"

    # The run is not resumable, and says what to do instead.
    got = (await client.get(f"{API}/label-runs/{failed.id}")).json()
    assert got["resumable"] is False
    assert "reproduction_retry_reason" in got["not_resumable_reason"]
    resumed = await client.post(f"{API}/label-runs/{failed.id}/resume")
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["error"]["code"] == "RUN_GATE_FAILED"

    # The plan reads failed, with the figures and the run that failed.
    planned = (await plan(client, probe_body(parsed))).json()
    gate = planned["reproduction"]
    assert gate["state"] == "failed" and gate["failed_run_id"] == failed.id
    assert gate["millm_auroc"] == pytest.approx(EXPECTED_AUROC, abs=1e-6)
    assert gate["link_id"] == made["id"] and "retry" in gate
    refused = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "REPRODUCTION_FAILED_BEFORE"

    # Something changed (another window): the check runs again.
    other = probe_body(parsed, probe={"probe_id": PROBE, "window": "prompt"})
    assert (await plan(client, other)).json()["reproduction"]["state"] == "will_run"

    # A deliberate retry, with a reason, recorded on the run.
    reason = "miLLM was redeployed with a fixed chat template"
    retry = probe_body(parsed, reproduction_retry_reason=reason)
    replanned = (await plan(client, retry)).json()["reproduction"]
    assert replanned["state"] == "will_run"
    assert replanned["retry_of"] == {
        "run_id": failed.id,
        "millm_auroc": pytest.approx(EXPECTED_AUROC, abs=1e-6),
        "reason": reason,
    }
    again = await client.post(f"{API}/label-runs", json=retry)
    assert again.status_code == 201, again.text
    snapshot = again.json()["endpoint_snapshot"]
    assert snapshot["reproduction_retry"]["reason"] == reason
    assert snapshot["reproduction_retry"]["run_id"] == failed.id
    assert snapshot["reproduction_retry"]["by"] == operator_name
    second = mh.run_until_done(again.json()["id"])
    assert second.error["code"] == "REPRODUCTION_FAILED"  # ran again, and failed again
    assert second.endpoint_snapshot["reproduction"]["retry_of"]["reason"] == reason


async def test_a_retry_reason_is_refused_outside_probe_runs(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, mh: Labeling
) -> None:
    body = {
        "input_version_id": raw_version(data_dir),
        "role": "judge",
        "rubric_id": "rb_x",
        "field_map": {"text": "inputs"},
        "reproduction_retry_reason": "why",
    }
    refused = await plan(client, body)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "PROBE_NOT_EXPECTED"


# --- finding 5: rows proven identical outrank role ------------------------------------------------


def test_a_content_link_outranks_a_counts_only_link_of_the_preferred_role(
    data_dir: Path, clean_db: None
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    counts_only_id = _link_row(version_id, "id_test", 10, 0.90)  # in-distribution, newer
    content_id = _link_row(version_id, "ood_eval", 0, 0.74, check_level="content")
    with sync_session_factory()() as db:
        target = reproduction.target_for(db, SOURCE)
    assert target.link_id == content_id and target.link_check_level == "content"
    assert target.choice is not None
    assert "content hash comes before a counts-only one" in target.choice["why"]
    alternatives = target.choice["alternatives"]
    assert [(a["link_id"], a["check_level"], a["role"]) for a in alternatives] == [
        (counts_only_id, "counts_only", "id_test")
    ]
    assert target.as_dict()["choice"]["chosen"]["link_id"] == content_id


async def _fail_once(
    client: httpx.AsyncClient, data_dir: Path, mh: Labeling, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, Any]:
    use_studio(monkeypatch, studio_with_view(0.55, (0.50, 0.60)))
    parsed = parsed_version(data_dir)
    await link(client, parsed)
    started = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    assert started.status_code == 201, started.text
    failed = mh.run_until_done(started.json()["id"])
    assert failed.error["code"] == "REPRODUCTION_FAILED"
    return parsed, failed


async def test_another_link_is_a_changed_check_and_runs_again(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed, failed = await _fail_once(client, data_dir, mh, monkeypatch)
    assert (await plan(client, probe_body(parsed))).json()["reproduction"]["state"] == "failed"
    # The same rows linked again from another version: a different target, so the check reruns.
    other = parsed_version(data_dir)
    newer = await link(client, other)
    gate = (await plan(client, probe_body(parsed))).json()["reproduction"]
    assert gate["state"] == "will_run" and gate["link_id"] == newer["id"]
    assert "retry_of" not in gate


async def test_an_unreported_revision_never_matches_a_failure(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mh.millm.resident["revision"] = None
    parsed, _ = await _fail_once(client, data_dir, mh, monkeypatch)
    gate = (await plan(client, probe_body(parsed))).json()["reproduction"]
    assert gate["state"] == "will_run"


async def test_the_worker_refuses_a_queued_run_whose_check_already_failed(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_studio(monkeypatch, studio_with_view(0.55, (0.50, 0.60)))
    parsed = parsed_version(data_dir)
    await link(client, parsed)
    # Both planned before either ran: the service could not know the first would fail.
    first = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    second = await client.post(f"{API}/label-runs", json=probe_body(parsed))
    assert first.status_code == 201 and second.status_code == 201
    assert mh.run_until_done(first.json()["id"]).error["code"] == "REPRODUCTION_FAILED"
    before = mh.millm.probe_score_calls
    refused = mh.run_until_done(second.json()["id"])
    assert refused.state == "failed" and refused.error["code"] == "REPRODUCTION_FAILED_BEFORE"
    assert first.json()["id"] in refused.error["message"]
    assert mh.millm.probe_score_calls == before  # nothing scored again


async def test_the_worker_refuses_to_rerun_a_run_whose_own_check_failed(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    mh: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor under the refused resume: a job that reaches the worker anyway (an old client, a
    replayed message) still does not run the same failed check again."""
    from src.core.ids import new_id
    from src.models.job import Job
    from src.models.label_run import LabelRun, LabelRunJob

    _, failed = await _fail_once(client, data_dir, mh, monkeypatch)
    with sync_session_factory()() as db:
        run = db.get(LabelRun, failed.id)
        assert run is not None
        job = Job(
            id=new_id("job"),
            kind="label_run",
            status="queued",
            progress=0.0,
            params={"label_run_id": run.id},
            started_by="Test Operator",
            started_by_origin="operator",
            required_model_id=LLAMA["name"],
        )
        db.add(job)
        db.flush()
        db.add(LabelRunJob(label_run_id=run.id, seq=1, job_id=job.id))
        run.state, run.error = "queued", None
        db.commit()
    before = mh.millm.probe_score_calls
    again = mh.run_until_done(failed.id)
    assert again.error["code"] == "REPRODUCTION_FAILED_BEFORE"
    assert mh.millm.probe_score_calls == before


def test_a_coverage_of_none_is_stored_as_sql_null_and_a_json_null_is_refused(
    data_dir: Path, clean_db: None
) -> None:
    """Control R6 found it: SQLAlchemy writes a Python None to JSONB as JSON ``null`` unless the
    column says ``none_as_null``, and 0021's CHECK (object or absent) then refuses the row with a
    500. A run with no coverage must store SQL NULL ("not recorded"); a JSON null is still refused.
    """
    from sqlalchemy import text

    from src.models.label_run import LabelRun
    from src.services.labeling_rules import fingerprint, identity_hash

    version_id = raw_version(data_dir)
    identity = {"protocol": "openai_scoring", "model_id": "m"}
    with sync_session_factory()() as db:
        run = LabelRun(
            id="lr_nullcoverage",
            kind="classifier",
            state="completed",
            input_version_id=version_id,
            field_map={"text": "inputs"},
            endpoint_snapshot={},
            sampling={},
            chunk_size=200,
            labeler_identity=identity,
            labeler_identity_hash=identity_hash(identity),
            labeler_fingerprint=fingerprint(identity, {}, "n/a", "single"),
            row_coverage=None,
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(run)
        db.commit()
        stored = db.execute(
            text("SELECT row_coverage IS NULL FROM dw_label_runs WHERE id = 'lr_nullcoverage'")
        ).scalar_one()
        assert stored is True
        with pytest.raises(Exception, match="row_coverage_object"):
            db.execute(
                text("UPDATE dw_label_runs SET row_coverage = 'null'::jsonb WHERE id = :i"),
                {"i": "lr_nullcoverage"},
            )

"""The three images run what the manifests ask of them, as non-root (ADR-024; task 15.1).

Images are built only in CI on the mirror, so nothing here builds one. Instead:

- ``docker-entrypoint.sh`` is RUN against stub ``alembic``/``uvicorn``/``celery`` executables that
  record their arguments, so each ``SERVICE_TYPE`` is checked for what it actually starts, and the
  refusals (unknown type, worker without queues, MCP before feature 010) exit non-zero.
- The Dockerfiles are read for the properties that matter at run time: a non-root ``USER``, the
  entrypoint, the ports, and the files the entrypoint needs actually being copied in.
- The module paths the entrypoint names are imported, so a renamed module fails here, not in a pod.
"""

from __future__ import annotations

import importlib
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
ENTRYPOINT = BACKEND / "docker-entrypoint.sh"


@pytest.fixture
def stubs(tmp_path: Path) -> tuple[Path, Path]:
    """A PATH directory whose alembic/uvicorn/celery append their argv to a log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("alembic", "uvicorn", "celery"):
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            f'echo "{name} $*" >> "{log}"\n'
            f'[ "{name}" = alembic ] && [ -n "${{ALEMBIC_FAILS:-}}" ] && exit 3\n'
            "exit 0\n"
        )
        script.chmod(0o755)
    return bin_dir, log


def run(stubs: tuple[Path, Path], **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir, log = stubs
    full = {"PATH": f"{bin_dir}:/usr/bin:/bin", **env}
    result = subprocess.run(
        ["sh", str(ENTRYPOINT)], env=full, capture_output=True, text=True, timeout=30
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


class TestEntrypoint:
    def test_it_is_executable_posix_sh(self) -> None:
        assert ENTRYPOINT.stat().st_mode & stat.S_IXUSR
        assert ENTRYPOINT.read_text().startswith("#!/bin/sh\n")

    def test_api_migrates_then_serves(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="api")
        assert result.returncode == 0, result.stderr
        assert calls[0] == "alembic upgrade head"
        assert calls[1].startswith("uvicorn src.main:app --host 0.0.0.0 --port 8000")
        assert len(calls) == 2

    def test_a_failed_migration_never_starts_the_api(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="api", ALEMBIC_FAILS="1")
        assert result.returncode != 0
        assert calls == ["alembic upgrade head"]

    def test_worker_consumes_exactly_its_queues(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(
            stubs,
            SERVICE_TYPE="worker",
            CELERY_QUEUES="curation",
            CELERY_WORKER_NAME="curation",
            CELERY_CONCURRENCY="2",
        )
        assert result.returncode == 0, result.stderr
        (call,) = calls
        assert call.startswith("celery -A src.core.celery_app worker -Q curation -n curation@%h")
        assert "--concurrency 2" in call

    def test_a_worker_without_queues_refuses(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="worker")
        assert result.returncode != 0 and "CELERY_QUEUES" in result.stderr
        assert calls == []

    def test_beat_runs_beat_without_migrating(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="beat")
        assert result.returncode == 0, result.stderr
        assert calls == [
            "celery -A src.core.celery_app beat --schedule /tmp/celerybeat-schedule --loglevel INFO"
        ]

    @pytest.mark.parametrize("service", ["mcp", "", "scheduler"])
    def test_unbuilt_or_unknown_service_types_refuse(
        self, stubs: tuple[Path, Path], service: str
    ) -> None:
        result, calls = run(stubs, SERVICE_TYPE=service)
        assert result.returncode != 0 and "REFUSING" in result.stderr
        assert calls == []

    def test_the_named_modules_exist(self) -> None:
        text = ENTRYPOINT.read_text()
        assert 'CELERY_APP="src.core.celery_app"' in text
        assert hasattr(importlib.import_module("src.core.celery_app"), "celery_app")
        assert hasattr(importlib.import_module("src.main"), "app")


def dockerfile(rel: str) -> str:
    return (REPO / rel).read_text()


def final_user(text: str) -> str:
    users = re.findall(r"^USER\s+(\S+)", text, re.M)
    assert users, "no USER instruction: the image runs as root"
    return users[-1]


class TestDockerfiles:
    @pytest.mark.parametrize(
        "rel", ["backend/Dockerfile", "frontend/Dockerfile", "datajuicer/Dockerfile"]
    )
    def test_every_image_runs_as_non_root(self, rel: str) -> None:
        user = final_user(dockerfile(rel)).split(":")[0]
        assert user not in {"root", "0"}, f"{rel} runs as {user}"

    def test_backend_and_datajuicer_share_a_uid_for_the_shared_volume(self) -> None:
        assert final_user(dockerfile("backend/Dockerfile")).split(":")[0] == "10001"
        assert "--uid 10001" in dockerfile("datajuicer/Dockerfile")

    def test_backend_copies_what_the_entrypoint_needs(self) -> None:
        text = dockerfile("backend/Dockerfile")
        for needed in (
            "requirements.txt",
            "alembic.ini",
            "alembic/",
            "src/",
            "docker-entrypoint.sh",
        ):
            assert re.search(rf"^COPY .*{re.escape(needed)}", text, re.M), needed
        assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in text
        bases = " ".join(re.findall(r"^FROM\s+(\S+)", text, re.M)).lower()
        assert "nvidia" not in bases and "cuda" not in bases, bases  # no GPU (BRD-03 §3)

    def test_backend_context_excludes_local_environments(self) -> None:
        ignored = (BACKEND / ".dockerignore").read_text().split()
        for path in (".venv/", ".python/", "tests/"):
            assert path in ignored

    def test_frontend_is_nginx_unprivileged_on_8080(self) -> None:
        text = dockerfile("frontend/Dockerfile")
        assert "FROM nginxinc/nginx-unprivileged" in text
        assert "EXPOSE 8080" in text
        assert "listen 8080;" in (REPO / "frontend" / "nginx.conf").read_text()

    def test_datajuicer_context_only_admits_the_runner(self) -> None:
        lines = (REPO / "datajuicer" / "Dockerfile.dockerignore").read_text().splitlines()
        rules = [line for line in lines if line and not line.startswith("#")]
        assert rules[0] == "*"
        admitted = {line[1:] for line in rules if line.startswith("!")}
        copied = set(re.findall(r"^COPY (\S+) ", dockerfile("datajuicer/Dockerfile"), re.M))
        assert (
            copied <= admitted
        ), f"COPY sources not admitted by the ignore file: {copied - admitted}"

    def test_no_local_build_helper_exists(self) -> None:
        # ADR-024: images are built only in CI. No script or compose service builds one.
        compose = (REPO / "docker-compose.yml").read_text()
        assert "build:" not in compose

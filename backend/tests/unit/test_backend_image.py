"""The three images run what the manifests ask of them, as non-root (ADR-024; task 15.1).

Images are built only in CI on the mirror, so nothing here builds one. Instead:

- ``docker-entrypoint.sh`` is RUN against stub ``alembic``/``uvicorn``/``celery``/``python``
  executables that record their arguments, so each ``SERVICE_TYPE`` is checked for what it actually
  starts, and the refusals (unknown type, worker without queues) exit non-zero.
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
    for name in ("alembic", "uvicorn", "celery", "python"):
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            f'echo "{name} $*" >> "{log}"\n'
            f'[ "{name}" = alembic ] && [ -n "${{ALEMBIC_FAILS:-}}" ] && exit 3\n'
            f'[ "{name}" = python ] && [ "$*" = "-m src.core.migrate upgrade" ] '
            '&& [ -n "${MIGRATE_FAILS:-}" ] && exit 3\n'
            f'[ "{name}" = python ] && [ "$*" = "-m src.core.migrate wait-for-head" ] '
            '&& [ -n "${WAIT_FAILS:-}" ] && exit 1\n'
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

    def test_api_migrates_through_the_guarded_runner_then_serves(
        self, stubs: tuple[Path, Path]
    ) -> None:
        """The API migrates with `python -m src.core.migrate upgrade` (bounded, names its blocker),
        never a bare `alembic upgrade head` (miStudio's incident of 2026-10-21), and never waits."""
        result, calls = run(stubs, SERVICE_TYPE="api")
        assert result.returncode == 0, result.stderr
        assert calls[0] == "python -m src.core.migrate upgrade"
        assert calls[1].startswith("uvicorn src.main:app --host 0.0.0.0 --port 8000")
        assert len(calls) == 2

    def test_a_failed_migration_never_starts_the_api(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="api", MIGRATE_FAILS="1")
        assert result.returncode != 0
        assert calls == ["python -m src.core.migrate upgrade"]
        assert "not starting the API" in result.stderr

    def test_worker_waits_for_the_schema_then_consumes_exactly_its_queues(
        self, stubs: tuple[Path, Path]
    ) -> None:
        result, calls = run(
            stubs,
            SERVICE_TYPE="worker",
            CELERY_QUEUES="curation",
            CELERY_WORKER_NAME="curation",
            CELERY_CONCURRENCY="2",
        )
        assert result.returncode == 0, result.stderr
        wait, call = calls
        assert wait == "python -m src.core.migrate wait-for-head"
        assert call.startswith("celery -A src.core.celery_app worker -Q curation -n curation@%h")
        assert "--concurrency 2" in call

    @pytest.mark.parametrize(
        "env",
        [{"SERVICE_TYPE": "worker", "CELERY_QUEUES": "default"}, {"SERVICE_TYPE": "beat"}],
        ids=["worker", "beat"],
    )
    def test_a_worker_or_beat_whose_schema_wait_fails_never_starts(
        self, stubs: tuple[Path, Path], env: dict[str, str]
    ) -> None:
        result, calls = run(stubs, WAIT_FAILS="1", **env)
        assert result.returncode != 0
        assert calls == ["python -m src.core.migrate wait-for-head"], "started on a stale schema"
        assert "not starting" in result.stderr

    def test_the_schema_wait_can_be_switched_off(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(
            stubs,
            SERVICE_TYPE="worker",
            CELERY_QUEUES="default",
            WAIT_FAILS="1",
            WAIT_FOR_SCHEMA="false",
        )
        assert result.returncode == 0, result.stderr
        assert len(calls) == 1 and calls[0].startswith("celery "), calls

    def test_a_worker_without_queues_refuses(self, stubs: tuple[Path, Path]) -> None:
        result, calls = run(stubs, SERVICE_TYPE="worker")
        assert result.returncode != 0 and "CELERY_QUEUES" in result.stderr
        assert calls == []

    def test_beat_waits_for_the_schema_and_runs_beat_without_migrating(
        self, stubs: tuple[Path, Path], tmp_path: Path
    ) -> None:
        result, calls = run(stubs, SERVICE_TYPE="beat", DATA_DIR=str(tmp_path / "data"))
        assert result.returncode == 0, result.stderr
        assert calls == [
            "python -m src.core.migrate wait-for-head",
            f"celery -A src.core.celery_app beat --schedule {tmp_path}/data/celerybeat/celerybeat-schedule"
            " --loglevel INFO",
        ]
        assert (tmp_path / "data" / "celerybeat").is_dir()

    def test_beats_schedule_is_on_the_data_volume_the_beat_container_mounts(self) -> None:
        """The schedule must outlive the pod: in /tmp a restart reset every interval, so a daily
        task never ran on a pod redeployed more than once a day (found 2026-10-21; miStudio and
        miForge had the same defect). The beat container must mount the default DATA_DIR."""
        import yaml

        manifest = ENTRYPOINT.parents[1] / "k8s" / "base" / "backend.yaml"
        for doc in yaml.safe_load_all(manifest.read_text()):
            if not doc or doc.get("kind") != "Deployment":
                continue
            for c in doc["spec"]["template"]["spec"]["containers"]:
                env = {e["name"]: e.get("value") for e in c.get("env", [])}
                if env.get("SERVICE_TYPE") == "beat":
                    data_dir = env.get("DATA_DIR", "/data/dataworks")
                    mounts = [m["mountPath"] for m in c.get("volumeMounts", [])]
                    assert data_dir in mounts, f"beat does not mount {data_dir}: {mounts}"
                    return
        raise AssertionError("no beat container in the manifest")

    def test_mcp_runs_the_mcp_server_without_migrating(self, stubs: tuple[Path, Path]) -> None:
        """Feature 010 (ADR-016): the MCP server, and no migration (it has no database)."""
        result, calls = run(stubs, SERVICE_TYPE="mcp")
        assert result.returncode == 0, result.stderr
        assert calls == ["python -m src.mcp_server"]

    @pytest.mark.parametrize("service", ["", "scheduler"])
    def test_unknown_service_types_refuse(self, stubs: tuple[Path, Path], service: str) -> None:
        result, calls = run(stubs, SERVICE_TYPE=service)
        assert result.returncode != 0 and "REFUSING" in result.stderr
        assert calls == []

    def test_the_named_modules_exist(self) -> None:
        text = ENTRYPOINT.read_text()
        assert 'CELERY_APP="src.core.celery_app"' in text
        assert hasattr(importlib.import_module("src.core.celery_app"), "celery_app")
        assert hasattr(importlib.import_module("src.main"), "app")
        assert hasattr(importlib.import_module("src.mcp_server.__main__"), "main")
        assert "python -m src.core.migrate upgrade" in text
        assert "python -m src.core.migrate wait-for-head" in text
        assert hasattr(importlib.import_module("src.core.migrate"), "main")


def dockerfile(rel: str) -> str:
    return (REPO / rel).read_text()


def final_user(text: str) -> str:
    users = re.findall(r"^USER\s+(\S+)", text, re.M)
    assert users, "no USER instruction: the image runs as root"
    return users[-1]


class TestDockerfiles:
    @pytest.mark.parametrize(
        "rel",
        [
            "backend/Dockerfile",
            "frontend/Dockerfile",
            "datajuicer/Dockerfile",
            "designer/Dockerfile",
            "runner/Dockerfile",
        ],
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
            "requirements-operators.txt",
            "constraints.txt",
            "alembic.ini",
            "alembic/",
            "src/",
            "docker-entrypoint.sh",
        ):
            assert re.search(rf"^COPY .*{re.escape(needed)}", text, re.M), needed
        assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in text
        # Feature 003 (T-10): third-party operator packages arrive only through this file.
        assert re.search(
            r"^RUN pip install -r /app/requirements-operators.txt -c /app/constraints.txt$",
            text,
            re.M,
        )
        # Every install is held to the whole resolved set, so the image, CI and the dev venv run
        # the same transitive versions (it floated per build until 2026-10-22).
        installs = re.findall(r"pip install -r (\S+)([^\n&]*)", text)
        assert [r for r, _ in installs] == [
            "/app/requirements.txt",
            "/app/requirements-operators.txt",
        ], installs
        for req, rest in installs:
            assert "-c /app/constraints.txt" in rest, f"{req} is installed without the constraints"
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

    @pytest.mark.parametrize("image", ["datajuicer", "designer", "runner"])
    def test_root_context_images_admit_exactly_what_they_copy(self, image: str) -> None:
        """Every COPY source (all of them, not only the first) is admitted by the image's own
        ignore file, directly or under an admitted directory; everything else is excluded."""
        lines = (REPO / image / "Dockerfile.dockerignore").read_text().splitlines()
        rules = [line for line in lines if line and not line.startswith("#")]
        assert rules[0] == "*"
        admitted = {line[1:] for line in rules if line.startswith("!")}
        sources: set[str] = set()
        for line in dockerfile(f"{image}/Dockerfile").splitlines():
            if line.startswith("COPY "):
                tokens = [t for t in line.split()[1:] if not t.startswith("--")]
                sources.update(tokens[:-1])

        def ok(source: str) -> bool:
            return source in admitted or any(
                a.endswith("/") and source.startswith(a) for a in admitted
            )

        missing = {s for s in sources if not ok(s)}
        assert not missing, f"COPY sources not admitted by the ignore file: {missing}"

    def test_no_local_build_helper_exists(self) -> None:
        # ADR-024: images are built only in CI. No script or compose service builds one.
        compose = (REPO / "docker-compose.yml").read_text()
        assert "build:" not in compose


# --- APP_BUILD: the build every publish manifest records (2026-10-20) --------------------------
#
# Settings.app_build (core/config.py) is producer.build in every manifest. Nothing set APP_BUILD
# before 2026-10-20, so every published manifest said "dev". The chain is: the mirror build names
# the build (docker-images.yml, step `app_build`) → passes it as a build argument → the backend
# Dockerfile bakes it into the environment → Settings reads it → the manifest records it
# (tests/integration/publishing/test_publish_fake_hub.py). Each link below fails on its own.

WORKFLOW = REPO / ".github" / "workflows" / "docker-images.yml"


def _instructions(text: str) -> list[tuple[str, str]]:
    """The Dockerfile's instructions of its LAST stage, continuation lines joined."""
    joined = re.sub(r"\\\n", " ", text)
    out: list[tuple[str, str]] = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        if word.upper() == "FROM":
            out = []
        out.append((word.upper(), rest.strip()))
    return out


def _build_steps() -> list[dict[str, object]]:
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    return list(data["jobs"]["build"]["steps"])


def _step(steps: list[dict[str, object]], step_id: str) -> tuple[int, dict[str, object]]:
    for index, item in enumerate(steps):
        if item.get("id") == step_id:
            return index, item
    raise AssertionError(f"docker-images.yml build job has no step id {step_id!r}")


class TestAppBuild:
    def test_the_backend_image_bakes_the_build_argument_into_its_environment(self) -> None:
        instructions = _instructions(dockerfile("backend/Dockerfile"))
        args = [
            i
            for i, (word, rest) in enumerate(instructions)
            if word == "ARG" and re.fullmatch(r"APP_BUILD(=\S+)?", rest)
        ]
        envs = [
            i
            for i, (word, rest) in enumerate(instructions)
            if word == "ENV" and re.fullmatch(r"APP_BUILD=\$\{?APP_BUILD\}?", rest)
        ]
        assert args, "the final stage declares no ARG APP_BUILD"
        assert envs, "the final stage never copies APP_BUILD into the environment"
        assert envs[-1] > args[-1], "ENV APP_BUILD must follow its ARG"

    def test_the_mirror_build_passes_the_build_to_the_image(self) -> None:
        steps = _build_steps()
        name_at, _ = _step(steps, "app_build")
        build_at, build = _step(steps, "build")
        assert name_at < build_at
        build_args = str(dict(build["with"]).get("build-args", ""))  # type: ignore[arg-type]
        lines = [line.strip() for line in build_args.splitlines() if line.strip()]
        assert "APP_BUILD=${{ steps.app_build.outputs.value }}" in lines

    @pytest.mark.parametrize(
        ("ref", "sha", "expected"),
        [
            ("refs/heads/main", "f" * 40, "f" * 40),
            ("refs/tags/v0.5.0", "e" * 40, "v0.5.0"),
        ],
    )
    def test_the_build_is_the_commit_or_the_release_tag(
        self, tmp_path: Path, ref: str, sha: str, expected: str
    ) -> None:
        _, named = _step(_build_steps(), "app_build")
        output = tmp_path / "github_output"
        output.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", str(named["run"])],
            env={
                "PATH": "/usr/bin:/bin",
                "GITHUB_REF": ref,
                "GITHUB_SHA": sha,
                "GITHUB_OUTPUT": str(output),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert output.read_text().splitlines() == [f"value={expected}"]

    def test_settings_read_the_build_from_the_environment(self) -> None:
        """A fresh process, as the image starts one: APP_BUILD in the environment is the
        manifest's producer.build."""
        import os
        import sys

        env = {**os.environ, "APP_BUILD": "abc1234"}
        code = (
            "from src.services.publishing.manifest_builder import producer; "
            "print(producer()['build'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "abc1234"

    def test_no_manifest_overrides_the_image_build(self) -> None:
        """An APP_BUILD in a ConfigMap or a container env would replace the image's value."""
        import yaml

        offenders: list[str] = []
        for path in sorted((REPO / "k8s").rglob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text()):
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") == "ConfigMap" and "APP_BUILD" in (doc.get("data") or {}):
                    offenders.append(f"{path.name}: ConfigMap {doc['metadata']['name']}")
                spec = (doc.get("spec") or {}).get("template", {}).get("spec", {})
                for container in (spec.get("containers") or []) + (
                    spec.get("initContainers") or []
                ):
                    for item in container.get("env") or []:
                        if item.get("name") == "APP_BUILD":
                            offenders.append(f"{path.name}: {container['name']}")
        assert not offenders, offenders

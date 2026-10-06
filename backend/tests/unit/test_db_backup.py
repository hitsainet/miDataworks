"""The nightly backup verifies before it counts and prunes only after a verified write (R-03.65,
ADR-026; Foundation tasks 17.1, 17.2).

The CronJob's own shell is RUN, as written in ``k8s/base/db-backup.yaml``, with stub ``pg_dump``
and ``pg_restore`` executables, against a scratch backup directory. The properties checked are the
ones whose failure is silent: a dump that is not a backup being published, a prune running after a
failed dump, retention touching files it does not own, and the restore notes going missing.
The expected tables are derived from the ORM, not restated.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "k8s" / "base" / "db-backup.yaml"


def cronjob() -> dict[str, Any]:
    (doc,) = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d and d["kind"] == "CronJob"]
    return dict(doc)


def pod() -> dict[str, Any]:
    spec: dict[str, Any] = cronjob()["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return spec


def container() -> dict[str, Any]:
    (c,) = pod()["containers"]
    return dict(c)


def env() -> dict[str, str]:
    return {e["name"]: e["value"] for e in container()["env"] if "value" in e}


def toc(tables: list[str]) -> str:
    lines = [";", "; Archive created at 2026-10-06", ";"]
    for i, table in enumerate(tables, start=3000):
        lines.append(f"{i}; 0 16400 TABLE DATA public {table} midataworks")
    return "\n".join(lines) + "\n"


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump").write_text(
        "#!/bin/sh\n"
        '[ -n "${PG_DUMP_FAILS:-}" ] && exit 1\n'
        'for a in "$@"; do case "$a" in --file=*) f="${a#--file=}";; esac; done\n'
        'printf "PGDMP-stub-archive-%s" "$$" > "$f"\n'
    )
    (bin_dir / "pg_restore").write_text('#!/bin/sh\ncat "$STUB_TOC_FILE"\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return bin_dir


def run_backup(
    tmp_path: Path, stubs: Path, tables: list[str], **overrides: str
) -> subprocess.CompletedProcess[str]:
    backups = tmp_path / "backups"
    toc_file = tmp_path / "toc.txt"
    toc_file.write_text(toc(tables))
    script = container()["command"][2]
    full_env = {
        **os.environ,
        **env(),
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "BACKUP_DIR": str(backups),
        "STUB_TOC_FILE": str(toc_file),
        **overrides,
    }
    return subprocess.run(
        ["bash", "-c", script], env=full_env, capture_output=True, text=True, timeout=60
    )


def required_tables() -> list[str]:
    return env()["REQUIRED_TABLES"].split()


def all_tables() -> list[str]:
    from src.core.database import Base

    return ["alembic_version", *sorted(Base.metadata.tables)]


def dumps(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "backups").glob("midataworks-*.dump"))


class TestTheScript:
    def test_a_good_dump_is_verified_published_and_logged(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        result = run_backup(tmp_path, stubs, all_tables())
        assert result.returncode == 0, result.stdout + result.stderr
        (dump,) = dumps(tmp_path)
        assert not list((tmp_path / "backups").glob("*.partial"))
        log = (tmp_path / "backups" / "backups.log").read_text()
        assert dump.name in log
        assert f"sha256={hashlib.sha256(dump.read_bytes()).hexdigest()}" in log

    def test_restore_notes_are_written_beside_the_archives(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        assert run_backup(tmp_path, stubs, all_tables()).returncode == 0
        notes = (tmp_path / "backups" / "RESTORE.md").read_text()
        assert notes.startswith("# Restoring a miDataworks dump")
        assert "--schema-only" in notes  # ENUM types before a selective restore (17.2)
        assert "ownerReferences" in notes  # the manual dump that is not killed (17.3)

    @pytest.mark.parametrize("missing", ["dw_jobs", "dw_app_settings", "alembic_version"])
    def test_a_dump_missing_a_required_table_is_refused_and_nothing_is_pruned(
        self, tmp_path: Path, stubs: Path, missing: str
    ) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        old = [backups / f"midataworks-2026010{i}T000000Z.dump" for i in range(1, 4)]
        for path in old:
            path.write_text("older dump")
        # Padded so the COUNT check passes and the by-name check is the one that must refuse.
        tables = [t for t in all_tables() if t != missing] + ["dw_padding_a", "dw_padding_b"]
        result = run_backup(tmp_path, stubs, tables, KEEP="1")
        assert result.returncode == 1 and f"no TABLE DATA entry for '{missing}'" in result.stdout
        assert dumps(tmp_path) == old, "a refused run pruned or published"
        assert not list(backups.glob("*.partial"))

    def test_too_few_tables_is_refused_even_when_every_required_one_is_there(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        # Every required table present, so only the COUNT check can refuse (control D07).
        tables = required_tables()
        assert len(tables) < int(env()["MIN_TABLES"])
        result = run_backup(tmp_path, stubs, tables)
        assert result.returncode == 1
        assert f"only {len(tables)} tables in the archive" in result.stdout
        assert dumps(tmp_path) == []

    def test_a_failed_pg_dump_publishes_and_prunes_nothing(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "midataworks-20260101T000000Z.dump").write_text("older dump")
        result = run_backup(tmp_path, stubs, all_tables(), PG_DUMP_FAILS="1", KEEP="0")
        assert result.returncode != 0
        assert [p.name for p in dumps(tmp_path)] == ["midataworks-20260101T000000Z.dump"]

    def test_retention_keeps_the_newest_and_touches_only_its_own_files(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        now = time.time()
        for i in range(5):
            path = backups / f"midataworks-2026010{i + 1}T000000Z.dump"
            path.write_text("older")
            os.utime(path, (now - 86400 * (10 - i), now - 86400 * (10 - i)))
        (backups / "someone-elses.dump").write_text("not ours")
        result = run_backup(tmp_path, stubs, all_tables(), KEEP="3")
        assert result.returncode == 0, result.stdout + result.stderr
        kept = [p.name for p in dumps(tmp_path)]
        assert len(kept) == 3
        assert "midataworks-20260105T000000Z.dump" in kept  # newest of the old ones
        assert "midataworks-20260101T000000Z.dump" not in kept
        assert (backups / "someone-elses.dump").exists()

    def test_keep_zero_still_keeps_the_newest(self, tmp_path: Path, stubs: Path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "midataworks-20260101T000000Z.dump").write_text("older")
        os.utime(backups / "midataworks-20260101T000000Z.dump", (1, 1))
        assert run_backup(tmp_path, stubs, all_tables(), KEEP="0").returncode == 0
        assert len(dumps(tmp_path)) == 1


class TestTheSchedule:
    def test_nightly_after_mistudio_and_millm_in_eastern_time(self) -> None:
        spec = cronjob()["spec"]
        assert spec["schedule"] == "10 4 * * *"
        assert spec["timeZone"] == "America/New_York"
        assert spec["concurrencyPolicy"] == "Forbid"

    def test_writes_to_the_separate_disk_on_the_pinned_node(self) -> None:
        (volume,) = pod()["volumes"]
        assert volume["hostPath"] == {
            "path": "/mnt/hdd2/pv/midataworks",
            "type": "DirectoryOrCreate",
        }
        assert pod()["nodeSelector"] == {"kubernetes.io/hostname": "mcs-lnxhost02"}
        (mount,) = container()["volumeMounts"]
        # BACKUP_DIR is the mount itself; a subdirectory would double the path (miLLM's first run).
        assert mount["mountPath"] == env()["BACKUP_DIR"] == "/backups"

    def test_keeps_thirty(self) -> None:
        assert env()["KEEP"] == "30"

    def test_pg_dump_matches_the_server_major_version(self) -> None:
        postgres = yaml.safe_load_all((REPO / "k8s" / "base" / "postgres.yaml").read_text())
        (deployment,) = [d for d in postgres if d and d["kind"] == "Deployment"]
        server = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        assert container()["image"] == server == "postgres:15"

    def test_required_tables_exist_in_the_schema(self) -> None:
        assert set(required_tables()) <= set(all_tables())
        assert int(env()["MIN_TABLES"]) <= len(all_tables())

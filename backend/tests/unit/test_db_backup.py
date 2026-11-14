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
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.source_tree import source_only

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


PIPE_CAPACITY = 65536  # Linux default pipe buffer; the most a writer can leave unread


def real_format_toc(tables: list[str]) -> str:
    """A table of contents shaped like ``pg_restore -l`` on this schema, at production size.

    Real archives list pre-data (FUNCTION, TABLE), then data (TABLE DATA), then post-data
    (CONSTRAINT, INDEX, FK CONSTRAINT, TRIGGER), one entry per line. ``pg_restore -l`` on a
    freshly migrated miDataworks database is 824 lines / 67,444 bytes with every TABLE DATA entry
    in its first quarter (measured 2026-10-08, pg_restore 15.19), which is the shape that matters:
    tens of kilobytes still to come after the line the verification looks for. ``toc()`` above is
    4 KB with nothing after the data section, and against it the old ``printf | grep -q`` check
    passed almost always and lost a scheduling race only occasionally (CI, 2026-10-08).
    """
    lines = [";", "; Archive created at 2026-10-08 12:52:40 UTC", ";     Format: CUSTOM", ";"]
    oid = 37519000
    for i, table in enumerate(tables, start=200):
        lines.append(f"{i}; 1259 {oid + i} TABLE public {table} midataworks")
    for i, table in enumerate(tables, start=5000):
        lines.append(f"{i}; 0 {oid + i} TABLE DATA public {table} midataworks")
    entry = 6000
    while sum(len(line) + 1 for line in lines) < 3 * PIPE_CAPACITY:
        for table in tables:
            for kind, name in (
                ("2606 CONSTRAINT", f"{table} {table}_pkey"),
                ("1259 INDEX", f"ix_{table}"),
                ("2606 FK CONSTRAINT", f"{table} fk_{table}"),
            ):
                entry += 1
                catalog, label = kind.split(" ", 1)
                lines.append(
                    f"{entry}; {catalog} {oid + entry} {label} public {name}_{entry} midataworks"
                )
    return "\n".join(lines) + "\n"


#: ``pg_restore -l`` prints the stub table of contents; ``pg_restore --data-only
#: --table=alembic_version --file=-`` prints the COPY block of the dumped schema version
#: (``$STUB_ALEMBIC_VERSION``, default the head this tree migrates to), as the real tool does.
PG_RESTORE_STUB = (
    "#!/bin/sh\n"
    'case "$*" in *--table=alembic_version*)\n'
    "  printf 'COPY public.alembic_version (version_num) FROM stdin;\\n%s\\n\\\\.\\n' "
    '"${STUB_ALEMBIC_VERSION:-0048}"; exit 0;;\n'
    "esac\n"
    'cat "$STUB_TOC_FILE"\n'
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backup"


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
    (bin_dir / "pg_restore").write_text(PG_RESTORE_STUB)
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return bin_dir


def run_backup(
    tmp_path: Path,
    stubs: Path,
    tables: list[str],
    make_toc: Callable[[list[str]], str] = toc,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    backups = tmp_path / "backups"
    toc_file = tmp_path / "toc.txt"
    toc_file.write_text(make_toc(tables))
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

    def test_a_production_sized_table_of_contents_is_accepted(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        # The CI flake of 2026-10-08 (d16ad2d), made deterministic. The check piped the table of
        # contents into `grep -q`, which exits at its first match; bash writes the TOC line by
        # line, so whatever was still unwritten met a closed pipe, printf died of SIGPIPE (141)
        # and `pipefail` turned a FOUND table into "REFUSING: no TABLE DATA entry". With more
        # than a pipe's capacity after the match the writer cannot finish first, so this fails
        # every time on the old check — and a real `pg_restore -l` is that shape every night.
        text = real_format_toc(all_tables())
        last_required = max(text.index(f"TABLE DATA public {t} ") for t in required_tables())
        assert len(text) - last_required > 2 * PIPE_CAPACITY, "fixture no longer exercises it"
        result = run_backup(tmp_path, stubs, all_tables(), make_toc=real_format_toc)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "REFUSING" not in result.stdout
        (dump,) = dumps(tmp_path)
        assert f"archive lists {len(all_tables())} TABLE DATA entries" in result.stdout
        assert dump.name in (tmp_path / "backups" / "backups.log").read_text()

    @pytest.mark.parametrize("make_toc", [toc, real_format_toc], ids=["short", "real_format"])
    @pytest.mark.parametrize("missing", ["dw_jobs", "dw_app_settings", "alembic_version"])
    def test_a_dump_missing_a_required_table_is_refused_and_nothing_is_pruned(
        self,
        tmp_path: Path,
        stubs: Path,
        missing: str,
        make_toc: Callable[[list[str]], str],
    ) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        old = [backups / f"midataworks-2026010{i}T000000Z.dump" for i in range(1, 4)]
        for path in old:
            path.write_text("older dump")
        # Padded so the COUNT check passes and the by-name check is the one that must refuse.
        tables = [t for t in all_tables() if t != missing] + ["dw_padding_a", "dw_padding_b"]
        result = run_backup(tmp_path, stubs, tables, make_toc, KEEP="1")
        assert result.returncode == 1 and f"no TABLE DATA entry for '{missing}'" in result.stdout
        assert dumps(tmp_path) == old, "a refused run pruned or published"
        assert not list(backups.glob("*.partial"))
        assert len(list(backups.glob("*.refused"))) == 1, "the refused dump is kept"

    def test_a_refused_dump_is_kept_and_reported_not_deleted(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Review 2026-11-08 (pre-existing defect): a refusal deleted the night's dump. It is now
        kept as ``<name>.refused`` beside the backups, logged as REFUSED, and the job still fails;
        nothing is published or pruned (control M70)."""
        tables = [t for t in all_tables() if t != "dw_jobs"]
        result = run_backup(tmp_path, stubs, tables)
        assert result.returncode == 1 and "no TABLE DATA entry for 'dw_jobs'" in result.stdout
        backups = tmp_path / "backups"
        [kept] = list(backups.glob("midataworks-*.dump.refused"))
        assert kept.read_text().startswith("PGDMP-stub-archive-"), "the dump's bytes are kept"
        assert not list(backups.glob("*.partial")) and dumps(tmp_path) == []
        assert "ALERT" in result.stdout and kept.name in result.stdout
        log = (backups / "backups.log").read_text()
        assert "REFUSED" in log and kept.name in log

    def test_the_020_tables_are_required_only_from_schema_0048(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Review 2026-11-08: the manifest reaches the cluster on gitops sync while 0048 waits for
        the new image. A dump at 0047 has no 020 tables and must still publish; at 0048 or later a
        missing one is refused (control M71)."""
        before = [t for t in all_tables() if not t.startswith("dw_legacy_name_")]
        for sub in ("a", "b", "c"):
            (tmp_path / sub).mkdir()
        older = run_backup(tmp_path / "a", stubs, before, STUB_ALEMBIC_VERSION="0047")
        assert older.returncode == 0, older.stdout + older.stderr
        assert "schema 0047" in older.stdout
        at_head = run_backup(tmp_path / "b", stubs, before, STUB_ALEMBIC_VERSION="0048")
        assert at_head.returncode == 1
        assert "no TABLE DATA entry for 'dw_legacy_name_uses'" in at_head.stdout
        later = run_backup(tmp_path / "c", stubs, all_tables(), STUB_ALEMBIC_VERSION="0051")
        assert later.returncode == 0, later.stdout

    def test_a_real_0047_dump_publishes(self, tmp_path: Path, stubs: Path) -> None:
        """The reviewer's case: ``pg_restore -l`` of a real 0047 database (Postgres 15)."""
        toc_0047 = (FIXTURES / "toc_0047.txt").read_text()
        assert "dw_legacy_name_uses" not in toc_0047
        result = run_backup(
            tmp_path, stubs, [], make_toc=lambda _: toc_0047, STUB_ALEMBIC_VERSION="0047"
        )
        assert result.returncode == 0, result.stdout[-600:]
        assert len(dumps(tmp_path)) == 1

    def test_two_schema_version_rows_are_refused_and_kept(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Review round 2: the chain is linear, so a dump holding two ``alembic_version`` rows is not
        a database this script knows how to judge (rows 0047 and 0048 with a 020 table dropped read
        as "schema 0047" and published). Refused, kept, logged (control M76)."""
        (stubs / "pg_restore").write_text(
            "#!/bin/sh\n"
            'case "$*" in *--table=alembic_version*)\n'
            "  printf 'COPY public.alembic_version (version_num) FROM stdin;\\n0047\\n0048\\n\\\\.\\n'; exit 0;;\n"
            "esac\n"
            'cat "$STUB_TOC_FILE"\n'
        )
        tables = [t for t in all_tables() if t != "dw_legacy_name_uses"]
        result = run_backup(tmp_path, stubs, tables)
        assert result.returncode == 1, result.stdout
        assert "2 alembic_version rows" in result.stdout, result.stdout
        backups = tmp_path / "backups"
        assert len(list(backups.glob("*.refused"))) == 1 and dumps(tmp_path) == []
        assert "REFUSED" in (backups / "backups.log").read_text()

    @pytest.mark.parametrize("fails_on", ["-l", "--table=alembic_version"])
    def test_a_failed_pg_restore_is_refused_and_kept(
        self, tmp_path: Path, stubs: Path, fails_on: str
    ) -> None:
        """Review round 2: under ``set -e`` a failed ``pg_restore`` (the table of contents, or the
        schema version) used to end the run with a ``.partial`` left behind, no REFUSED line and no
        ALERT. Both go through ``refuse`` (controls M77, M77b)."""
        (stubs / "pg_restore").write_text(
            "#!/bin/sh\n"
            f'case "$*" in *{fails_on}*) echo "pg_restore: error: corrupt" >&2; exit 1;; esac\n'
            'case "$*" in *--table=alembic_version*)\n'
            "  printf 'COPY public.alembic_version (version_num) FROM stdin;\\n0048\\n\\\\.\\n'; exit 0;;\n"
            "esac\n"
            'cat "$STUB_TOC_FILE"\n'
        )
        result = run_backup(tmp_path, stubs, all_tables())
        assert result.returncode == 1
        assert "REFUSING" in result.stdout and "ALERT" in result.stdout, result.stdout
        backups = tmp_path / "backups"
        assert len(list(backups.glob("*.refused"))) == 1
        assert not list(backups.glob("*.partial"))
        assert "REFUSED" in (backups / "backups.log").read_text()

    def test_leftovers_are_pruned_and_logged_even_on_a_refusing_night(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Review round 2: retention never deleted ``.refused`` or ``.partial`` files, so a refusing
        setting (backoffLimit 1) added up to two full dumps a night until the free-space check
        stopped every backup. The newest KEEP_REFUSED (3) refused dumps stay; older ones and any
        ``.partial`` older than 24 h go, each logged, on every run, a refusing one included
        (control M78)."""
        import os as _os
        import time as _time

        backups = tmp_path / "backups"
        backups.mkdir()
        now = _time.time()
        refused = []
        for i in range(5):
            path = backups / f"midataworks-2026010{i}T000000Z.dump.refused"
            path.write_text("refused")
            _os.utime(path, (now - (10 - i) * 3600, now - (10 - i) * 3600))
            refused.append(path)
        stale = backups / "midataworks-20260101T000000Z.dump.partial"
        stale.write_text("partial")
        _os.utime(stale, (now - 25 * 3600, now - 25 * 3600))
        fresh = backups / "midataworks-20260109T000000Z.dump.partial"
        fresh.write_text("partial")
        _os.utime(fresh, (now - 3600, now - 3600))
        tables = [t for t in all_tables() if t != "dw_jobs"]
        result = run_backup(tmp_path, stubs, tables)
        assert result.returncode == 1, "this night refuses too"
        kept = sorted(p.name for p in backups.glob("*.refused"))
        assert len(kept) == 3, kept
        assert refused[0].name not in kept and refused[1].name not in kept
        assert refused[2].name not in kept, "tonight's refusal is one of the newest three"
        assert not stale.exists() and fresh.exists()
        log = (backups / "backups.log").read_text()
        for gone in (refused[0], refused[1], refused[2], stale):
            assert f"PRUNED\t{gone.name}" in log, (gone.name, log)

    def test_an_unreadable_schema_version_is_refused_and_kept(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        result = run_backup(tmp_path, stubs, all_tables(), STUB_ALEMBIC_VERSION="not-a-version")
        assert result.returncode == 1 and "cannot read the schema version" in result.stdout
        assert list((tmp_path / "backups").glob("*.refused"))

    def test_a_refusal_is_final(self, tmp_path: Path, stubs: Path) -> None:
        """Review round 2 (2026-11-08, survivor B02): ``refuse`` must end the run itself. Without its
        ``exit 1`` the run went on past the refusal and failed only later, by accident of
        ``pipefail`` on the checksum line; a second missing table was refused AGAIN, appending a
        second REFUSED line for one dump. Exactly one refusal per run, and nothing after it."""
        missing = {"dw_jobs", "dw_approvals"}
        tables = [t for t in all_tables() if t not in missing] + ["dw_padding_a", "dw_padding_b"]
        (tmp_path / "two").mkdir()
        result = run_backup(tmp_path / "two", stubs, tables)
        assert result.returncode == 1
        assert result.stdout.count("REFUSING") == 1, result.stdout
        log = (tmp_path / "two" / "backups" / "backups.log").read_text()
        assert log.count("\tREFUSED\t") == 1, log
        (tmp_path / "few").mkdir()
        few = run_backup(tmp_path / "few", stubs, required_tables())
        assert few.returncode == 1 and few.stdout.count("REFUSING") == 1
        refused_at = few.stdout.index("REFUSING")
        assert "schema " not in few.stdout[refused_at:], "the run went on after the count refusal"

    def test_an_empty_schema_version_table_is_refused_and_kept(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Review round 2 (survivor B10): an EMPTY ``alembic_version`` (a COPY block with no row) is
        refused through ``refuse`` (kept, logged, alerted), not by a later arithmetic error that
        leaves a ``.partial`` and no REFUSED line."""
        (stubs / "pg_restore").write_text(
            "#!/bin/sh\n"
            'case "$*" in *--table=alembic_version*)\n'
            "  printf 'COPY public.alembic_version (version_num) FROM stdin;\\n\\\\.\\n\\n\\n'; exit 0;;\n"
            "esac\n"
            'cat "$STUB_TOC_FILE"\n'
        )
        result = run_backup(tmp_path, stubs, all_tables())
        assert result.returncode == 1 and "cannot read the schema version" in result.stdout, (
            result.stdout + result.stderr
        )
        backups = tmp_path / "backups"
        assert len(list(backups.glob("*.refused"))) == 1 and not list(backups.glob("*.partial"))
        assert "REFUSED" in (backups / "backups.log").read_text()

    def test_a_full_disk_is_refused_in_the_log_too(self, tmp_path: Path, stubs: Path) -> None:
        """Review round 3 (pre-existing): the free-space refusal said so only on stdout, so the
        backups.log beside the dumps showed nothing for that night. It now writes a REFUSED line
        like every other refusal, and nothing is dumped (control M82)."""
        (stubs / "df").write_text(
            "#!/bin/sh\nprintf 'Filesystem 1048576-blocks Used Available Capacity Mounted on\\n"
            "/dev/x 2000 1990 10 99%% /backups\\n'\n"
        )
        (stubs / "df").chmod(0o755)
        result = run_backup(tmp_path, stubs, all_tables())
        assert result.returncode == 1 and "under 1 GB free" in result.stdout
        backups = tmp_path / "backups"
        log = (backups / "backups.log").read_text()
        assert "\tREFUSED\t" in log and "under 1 GB free" in log, log
        assert dumps(tmp_path) == [] and not list(backups.glob("*.partial"))

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
        # BACKUP_DIR is the mount itself; a subdirectory would double the path (miServer's first run).
        assert mount["mountPath"] == env()["BACKUP_DIR"] == "/backups"

    def test_keeps_thirty(self) -> None:
        assert env()["KEEP"] == "30"

    def test_pg_dump_matches_the_server_major_version(self) -> None:
        postgres = yaml.safe_load_all((REPO / "k8s" / "base" / "postgres.yaml").read_text())
        (deployment,) = [d for d in postgres if d and d["kind"] == "Deployment"]
        server = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        assert container()["image"] == server == "postgres:15"

    def test_a_hung_run_is_ended_so_forbid_cannot_suppress_later_nights(self) -> None:
        """Review round 3 (pre-existing): with concurrencyPolicy Forbid, one hung run blocks every
        later night silently. The Job has a deadline well above a real run (control M83)."""
        spec = cronjob()["spec"]
        assert spec["concurrencyPolicy"] == "Forbid"
        deadline = spec["jobTemplate"]["spec"].get("activeDeadlineSeconds")
        assert isinstance(deadline, int) and 3600 <= deadline <= 6 * 3600, deadline

    def test_required_tables_exist_in_the_schema(self) -> None:
        assert set(required_tables()) <= set(all_tables())
        assert int(env()["MIN_TABLES"]) <= len(all_tables())

    def test_required_tables_include_the_020_tables(self) -> None:
        """Feature 020: the old-name evidence is what P7 retires a name on; a nightly dump that
        silently lost it would lose the 7-day record (FTID §4.1)."""
        since = dict(entry.split(":", 1) for entry in env()["REQUIRED_TABLES_SINCE"].split())
        assert set(since["0048"].split(",")) == {
            "dw_legacy_name_uses",
            "dw_legacy_name_write_errors",
        }
        assert not {"dw_legacy_name_uses", "dw_legacy_name_write_errors"} & set(
            required_tables()
        ), "required unconditionally, they would refuse every 0047 dump before 0048 runs"

    def test_an_empty_table_is_listed_in_the_dump_toc(
        self, tmp_path: Path, migrated_database: None
    ) -> None:
        """The 020 tables are EMPTY on a fresh install; the script's name check must still find
        them. A real ``pg_dump -Fc`` of the test database (empty 020 tables) is read back with
        ``pg_restore -l`` and the CronJob's own script is run on that table of contents.
        Verified by hand on 2026-11-08 against pg_dump 15.19 (TABLE DATA entries present for
        both empty tables); here it needs the client tools on PATH."""
        import shutil

        from sqlalchemy.engine import make_url

        from src.core.config import get_settings

        if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
            # CI must run it: GitHub's ubuntu runner ships the PostgreSQL client tools, so a missing
            # pg_dump there is a broken image, never a reason to skip (the skip would be silent).
            if os.environ.get("CI") == "true":
                pytest.fail(
                    "pg_dump/pg_restore missing on the CI runner: this check must run there"
                )
            pytest.skip("pg_dump/pg_restore not on PATH (verified by hand, 0xcc/reviews/020_P0)")
        url = make_url(get_settings().database_url_sync)
        dump = tmp_path / "real.dump"
        pg_env = {**os.environ, "PGPASSWORD": url.password or ""}
        subprocess.run(
            [
                "pg_dump",
                "-Fc",
                f"--file={dump}",
                "-h",
                url.host or "localhost",
                "-p",
                str(url.port or 5432),
                "-U",
                url.username or "",
                url.database or "",
            ],
            env=pg_env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        listed = subprocess.run(
            ["pg_restore", "-l", str(dump)], check=True, capture_output=True, text=True
        ).stdout
        for table in ("dw_legacy_name_uses", "dw_legacy_name_write_errors"):
            assert f"TABLE DATA public {table} " in listed, table
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "pg_dump").write_text(
            '#!/bin/sh\nfor a in "$@"; do case "$a" in --file=*) f="${a#--file=}";; esac; done\n'
            'printf x > "$f"\n'
        )
        (stubs / "pg_restore").write_text(PG_RESTORE_STUB)
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        result = run_backup(tmp_path, stubs, [], make_toc=lambda _: listed)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_script_reads_the_schema_version_of_a_real_dump(
    tmp_path: Path, migrated_database: None
) -> None:
    """Review round 2 (survivors B07, B11): every other test answers the version read with a stub
    that prints exactly one row and nothing after ``\\.``, so the awk that parses it could print
    every line, or the last, and stay green. Here the CronJob's script runs with the REAL
    ``pg_restore`` on a real ``pg_dump -Fc`` of the migrated test database (only ``pg_dump`` is
    replaced, by a copy of that dump), and must read the head revision and publish."""
    import shutil

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import make_url

    from src.core.config import get_settings

    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        if os.environ.get("CI") == "true":
            pytest.fail("pg_dump/pg_restore missing on the CI runner: this check must run there")
        pytest.skip("pg_dump/pg_restore not on PATH")
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    url = make_url(get_settings().database_url_sync)
    real = tmp_path / "real.dump"
    subprocess.run(
        [
            "pg_dump",
            "-Fc",
            f"--file={real}",
            "-h",
            url.host or "localhost",
            "-p",
            str(url.port or 5432),
            "-U",
            url.username or "",
            url.database or "",
        ],
        env={**os.environ, "PGPASSWORD": url.password or ""},
        check=True,
        capture_output=True,
        timeout=120,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump").write_text(
        '#!/bin/sh\nfor a in "$@"; do case "$a" in --file=*) f="${a#--file=}";; esac; done\n'
        f'cp "{real}" "$f"\n'
    )
    (bin_dir / "pg_dump").chmod(0o755)
    result = run_backup(tmp_path, bin_dir, [], make_toc=lambda _: "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"schema {head}: requiring " in result.stdout, result.stdout
    assert len(dumps(tmp_path)) == 1


def test_two_version_rows_in_a_real_dump_are_refused(
    tmp_path: Path, migrated_database: None
) -> None:
    """Review round 2: a real ``pg_dump`` of a database whose ``alembic_version`` holds 0047 then
    0048, with a 020 table's rows' table dropped from the requirement's sight, read by the REAL
    ``pg_restore``: refused, never published as "schema 0047"."""
    import shutil

    from sqlalchemy import text as sql
    from sqlalchemy.engine import make_url

    from src.core.config import get_settings
    from src.core.database import get_sync_engine

    if not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        if os.environ.get("CI") == "true":
            pytest.fail("pg_dump/pg_restore missing on the CI runner: this check must run there")
        pytest.skip("pg_dump/pg_restore not on PATH")
    engine = get_sync_engine()
    with engine.begin() as conn:
        head = conn.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
        conn.execute(sql("DELETE FROM alembic_version"))
        conn.execute(sql("INSERT INTO alembic_version VALUES ('0047')"))
        conn.execute(sql("INSERT INTO alembic_version VALUES (:h)"), {"h": head})
    url = make_url(get_settings().database_url_sync)
    real = tmp_path / "two.dump"
    try:
        subprocess.run(
            [
                "pg_dump",
                "-Fc",
                f"--file={real}",
                "-h",
                url.host or "localhost",
                "-p",
                str(url.port or 5432),
                "-U",
                url.username or "",
                url.database or "",
            ],
            env={**os.environ, "PGPASSWORD": url.password or ""},
            check=True,
            capture_output=True,
            timeout=120,
        )
    finally:
        with engine.begin() as conn:
            conn.execute(sql("DELETE FROM alembic_version"))
            conn.execute(sql("INSERT INTO alembic_version VALUES (:h)"), {"h": head})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump").write_text(
        '#!/bin/sh\nfor a in "$@"; do case "$a" in --file=*) f="${a#--file=}";; esac; done\n'
        f'cp "{real}" "$f"\n'
    )
    (bin_dir / "pg_dump").chmod(0o755)
    result = run_backup(tmp_path, bin_dir, [], make_toc=lambda _: "")
    assert result.returncode == 1, result.stdout
    assert "2 alembic_version rows" in result.stdout, result.stdout
    assert dumps(tmp_path) == []


class TestTheLeftoverPrune:
    """Review round 3 (2026-11-08): the prune added in round 2 deletes files from the only recovery
    path. It must never delete this run's own refused dump, never cost the night's dump when it
    fails itself, and touch only regular files directly in BACKUP_DIR whatever the names or cwd."""

    @staticmethod
    def _aged(path: Path, hours: float) -> Path:
        stamp = time.time() - hours * 3600
        os.utime(path, (stamp, stamp))
        return path

    def test_a_night_that_fails_before_refusing_still_prunes_leftovers(
        self, tmp_path: Path, stubs: Path
    ) -> None:
        """Survivor B01: the start-of-run prune. pg_dump fails, so neither ``refuse`` nor
        retention runs; the leftovers must still go (they are what fills the disk)."""
        backups = tmp_path / "backups"
        backups.mkdir()
        refused = [backups / f"midataworks-2026010{i}T000000Z.dump.refused" for i in range(1, 6)]
        for i, path in enumerate(refused, start=1):
            path.write_text("r")
            self._aged(path, 100 - i)
        stale = backups / "midataworks-20251201T000000Z.dump.partial"
        stale.write_text("p")
        self._aged(stale, 25)
        result = run_backup(tmp_path, stubs, all_tables(), PG_DUMP_FAILS="1")
        assert result.returncode != 0
        left = sorted(p.name for p in backups.glob("*.refused"))
        assert left == [p.name for p in refused[2:]], left
        assert not stale.exists()
        log = (backups / "backups.log").read_text()
        assert f"PRUNED\t{refused[0].name}" in log and f"PRUNED\t{stale.name}" in log

    @pytest.mark.parametrize("case", ["keep_refused_zero", "clock_skew"])
    def test_tonights_refused_dump_is_never_pruned(
        self, tmp_path: Path, stubs: Path, case: str
    ) -> None:
        """Defect R3-1: ``refuse`` says the dump "is kept ... for inspection", then its own prune
        deleted it when KEEP_REFUSED was 0 (or empty), or when KEEP_REFUSED older refused dumps
        carried later mtimes (a clock set back, a copy with ``-p``). A refusal may be false (the
        SIGPIPE refusals of 2026-10-08 were good dumps): tonight's is the night's only copy."""
        backups = tmp_path / "backups"
        backups.mkdir()
        keep = env()["KEEP_REFUSED"]
        if case == "keep_refused_zero":
            keep = "0"
        else:
            for i in range(1, 4):
                path = backups / f"midataworks-2026010{i}T000000Z.dump.refused"
                path.write_text("r")
                self._aged(path, -24 * i)  # written "in the future" by a clock that was ahead
        tables = [t for t in all_tables() if t != "dw_jobs"]
        result = run_backup(tmp_path, stubs, tables, KEEP_REFUSED=keep)
        assert result.returncode == 1 and "ALERT" in result.stdout
        named = re.search(r"kept as (\S+\.dump\.refused)", result.stdout)
        assert named, result.stdout
        tonight = backups / named.group(1)
        assert tonight.exists(), f"tonight's refused dump was pruned: {result.stdout}"
        assert tonight.read_text().startswith("PGDMP-stub-archive-")
        assert f"PRUNED\t{tonight.name}" not in (backups / "backups.log").read_text()

    @pytest.mark.parametrize("case", ["partial_directory", "keep_unset"])
    def test_a_prune_failure_never_costs_the_nights_dump(
        self, tmp_path: Path, stubs: Path, case: str
    ) -> None:
        """Defect R3-2: under ``set -e`` any failure in the start-of-run prune (an ``rm`` that
        fails, a log append on a full disk, KEEP_REFUSED unset) ended the run BEFORE the dump, with
        no REFUSED line and no ALERT, and again every night after. The dump must still be taken."""
        backups = tmp_path / "backups"
        backups.mkdir()
        if case == "partial_directory":
            odd = backups / "midataworks-20251101T000000Z.dump.partial"
            odd.mkdir()
            self._aged(odd, 25)
        script_env = {k: v for k, v in env().items() if k != "KEEP_REFUSED"}
        if case != "keep_unset":
            script_env["KEEP_REFUSED"] = env()["KEEP_REFUSED"]
        result = self._run(tmp_path, stubs, script_env, cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(dumps(tmp_path)) == 1, "the night's dump was not taken"
        if case == "partial_directory":
            assert odd.is_dir()
            log = (backups / "backups.log").read_text()
            assert f"PRUNED\t{odd.name}" not in log, "a removal that failed was logged as PRUNED"

    def test_the_prune_stays_out_of_subdirectories(self, tmp_path: Path, stubs: Path) -> None:
        """Survivor B07: ``-maxdepth 1``. A stale-looking partial in a subdirectory is not ours."""
        backups = tmp_path / "backups"
        (backups / "elsewhere").mkdir(parents=True)
        nested = backups / "elsewhere" / "midataworks-20251101T000000Z.dump.partial"
        nested.write_text("not ours")
        self._aged(nested, 48)
        assert run_backup(tmp_path, stubs, all_tables()).returncode == 0
        assert nested.exists()

    def test_the_refused_order_is_by_write_time(self, tmp_path: Path, stubs: Path) -> None:
        """Survivor B05: "newest" is when the dump was written (mtime), and the fixtures named and
        aged their dumps in the same order, so ordering by NAME passed too. Names and times
        disagree here; the oldest-written is the one pruned. (If the operator prefers the name
        stamp, flip this test with the code; it pins the choice, not which choice.)"""
        backups = tmp_path / "backups"
        backups.mkdir()
        ages = {"20260104": 96, "20260103": 72, "20260102": 48, "20260101": 1}
        for day, hours in ages.items():
            path = backups / f"midataworks-{day}T000000Z.dump.refused"
            path.write_text("r")
            self._aged(path, hours)
        assert run_backup(tmp_path, stubs, all_tables()).returncode == 0
        left = sorted(p.name[12:20] for p in backups.glob("*.refused"))
        assert left == ["20260101", "20260102", "20260103"], left

    def test_no_file_name_reaches_a_published_dump(self, tmp_path: Path, stubs: Path) -> None:
        """Defect R3-3 (latent): the prune parsed ``ls``/``find`` output line by line, so a refused
        name holding a newline, or a refused DIRECTORY (``ls`` lists its contents), fed bare names
        to ``rm`` relative to the working directory. The image's cwd is ``/``, so production is
        safe today; with cwd = BACKUP_DIR both deleted a published dump and logged it as PRUNED."""
        backups = tmp_path / "backups"
        backups.mkdir()
        published = [
            backups / "midataworks-20260101T000000Z.dump",
            backups / "midataworks-20260102T000000Z.dump",
        ]
        for path in published:
            path.write_text("a published dump")
            self._aged(path, 12)
        for i in range(1, 4):
            (backups / f"midataworks-2026020{i}T000000Z.dump.refused").write_text("r")
        hostile = backups / f"midataworks-e\n{published[0].name}\nx.dump.refused"
        hostile.write_text("r")
        self._aged(hostile, 24 * 30)
        folder = backups / "midataworks-20251101T000000Z.dump.refused"
        folder.mkdir()
        (folder / published[1].name).write_text("a copy taken for inspection")
        self._aged(folder, 24 * 31)
        result = self._run(tmp_path, stubs, env(), cwd=backups)
        assert result.returncode == 0, result.stdout + result.stderr
        for path in published:
            assert path.exists(), f"{path.name} deleted by the prune: {result.stdout}"
            assert f"PRUNED\t{path.name}" not in (backups / "backups.log").read_text()

    @staticmethod
    def _run(
        tmp_path: Path, stubs: Path, script_env: dict[str, str], cwd: Path, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        toc_file = tmp_path / "toc.txt"
        toc_file.write_text(toc(all_tables()))
        base = {k: v for k, v in os.environ.items() if k != "KEEP_REFUSED"}
        full_env = {
            **base,
            **script_env,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "BACKUP_DIR": str(tmp_path / "backups"),
            "STUB_TOC_FILE": str(toc_file),
            **overrides,
        }
        return subprocess.run(
            ["bash", "-c", container()["command"][2]],
            env=full_env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )


class TestTheManualDump:
    """R-03.65's second half: a manual dump strips the CronJob owner reference (000 FTASKS 18.9).

    The filter the runbook and the manifest's note tell the operator to pipe ``kubectl create job
    --from=cronjob/...`` through is RUN here on a Job carrying an owner reference. Until 2026-10-21
    only the restore notes' mention of ``ownerReferences`` was checked, never the filter itself.
    """

    FILTER = re.compile(r'python3 -c "([^"]*ownerReferences[^"]*)"')

    def filters(self, text: str) -> list[str]:
        return self.FILTER.findall(text)

    def test_the_manifest_and_the_runbook_give_one_and_the_same_filter(self) -> None:
        manifest = self.filters(MANIFEST.read_text())
        runbook = self.filters(source_only("docs/RUNBOOK.md").read_text())
        assert len(manifest) == 1, manifest
        assert runbook == manifest

    def test_the_filter_drops_the_owner_and_keeps_the_job(self) -> None:
        (snippet,) = self.filters(MANIFEST.read_text())
        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "manual-dump-1",
                "ownerReferences": [{"kind": "CronJob", "name": "midataworks-db-backup"}],
                "annotations": {"cronjob.kubernetes.io/instantiate": "manual"},
            },
            "spec": {"template": {"spec": {"restartPolicy": "Never"}}},
        }
        out = subprocess.run(
            ["python3", "-c", snippet],
            input=json.dumps(job),
            capture_output=True,
            text=True,
            check=True,
        )
        stripped = json.loads(out.stdout)
        assert "ownerReferences" not in stripped["metadata"]
        assert stripped["metadata"]["name"] == "manual-dump-1"
        assert stripped["spec"] == job["spec"]

# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_no_dumps_or_secrets_tracked.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Changes: tracked-file floor 500 -> 50 (a new repository), the backups script reference removed, plus .env and data/venv checks.
"""No database dump, and no credential, may enter git again.

miStudio MIS-E2E-008 / -143: five `backups/*.sql.gz` dumps and an SSH password were
committed there and then mirrored to a public repository. They cannot be withdrawn:
GitHub serves a commit by SHA whether or not anything references it, so a
force-push does not un-publish. Rotation and prevention are the only moves
left, and this is the prevention half.

Checks the git INDEX, not the working tree: what matters is that nothing of the
kind is tracked and that it cannot be added by accident. miDataworks adds `.env`
files and the dataset directory to the same rule.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _tracked():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert len(out) > 50, f"only {len(out)} tracked files — the git call is broken"
    return out


class TestNoDumpsAreTracked:
    def test_no_sql_dump_is_tracked(self):
        dumps = [f for f in _tracked() if f.endswith((".sql.gz", ".dump", ".sql"))]
        # A schema-only .sql fixture is fine; a dump of the live database is not.
        offenders = [f for f in dumps if "backup" in f.lower() or f.endswith(".sql.gz")]
        assert not offenders, (
            f"{offenders} are tracked. A database dump in git is permanently "
            f"public once mirrored — it cannot be withdrawn later."
        )

    def test_the_backups_directory_is_ignored(self):
        ignored = subprocess.run(
            ["git", "check-ignore", "backups/example.sql.gz"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert (
            ignored.returncode == 0
        ), "backups/ is not gitignored, so the next `git add -A` would commit a database dump"

    def test_the_check_would_notice_a_tracked_dump(self):
        """The scan must be able to fail — it looks at a real file list."""
        assert any(f.endswith(".py") for f in _tracked()), "git ls-files returned nothing usable"


class TestNoCredentialIsTracked:
    """MIS-E2E-143's prevention half: a literal assignment, not a var or a ref."""

    # The keyword may be embedded in a longer identifier — the published
    # credential was `K8S_PASS`, where `_` is a word character so a `\b`
    # anchor before `PASS` never matches. That is how the first version of
    # this pattern failed its own control.
    PATTERN = re.compile(r"""(?ix)
        (?:^|[\s;&|(])                                   # start of a statement
        [A-Za-z0-9_]*                                     # optional prefix: K8S_
        (?:passwd|password|pass|secret|token|api_?key)
        [A-Za-z0-9_]*                                     # optional suffix
        \s*=\s*
        ['"][^'"$\{\s]{3,}['"]                             # a literal, not ${VAR}
        """)

    #: Files where such an assignment is legitimate — examples, and this test.
    ALLOWED_SUFFIXES = (".example", ".sample", ".md", ".lock")
    ALLOWED_NAMES = {
        "test_no_dumps_or_secrets_tracked.py",  # this file states the pattern
    }

    def test_no_shell_or_yaml_file_assigns_a_literal_credential(self):
        offenders = []
        for rel in _tracked():
            if not rel.endswith((".sh", ".yml", ".yaml", ".env")):
                continue
            if rel.endswith(self.ALLOWED_SUFFIXES) or Path(rel).name in self.ALLOWED_NAMES:
                continue
            path = REPO / rel
            if not path.exists():
                continue
            for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if self.PATTERN.search(line):
                    offenders.append(f"{rel}:{lineno}")
        assert not offenders, (
            "literal credential assignments are tracked; they publish to the "
            f"mirror and cannot be withdrawn: {offenders}"
        )

    def test_the_pattern_matches_the_credential_that_was_published(self):
        """Negative control baked in: the historical value must be caught."""
        assert self.PATTERN.search(
            'K8S_PASS="pass"'
        ), "the pattern no longer matches the exact assignment this exists for"
        assert not self.PATTERN.search(
            'K8S_PASS="${K8S_PASS}"'
        ), "an environment reference is not a committed credential"
        assert not self.PATTERN.search('K8S_PASS=""'), "an empty default is fine"


class TestNoEnvOrDataIsTracked:
    """miDataworks additions: a real .env file and the prototype's downloaded data stay out."""

    def test_no_env_file_is_tracked(self):
        envs = [
            f for f in _tracked() if Path(f).name.startswith(".env") and not f.endswith(".example")
        ]
        assert not envs, f"{envs} are tracked; .env files hold live configuration"

    def test_the_dataset_and_venv_directories_are_ignored(self):
        for probe in (
            "dataset/raw/x.parquet",
            "venv/bin/python",
            "backend/.venv/bin/python",
            "frontend/node_modules/x/index.js",
            ".env",
            ".env.local",
        ):
            ignored = subprocess.run(["git", "check-ignore", probe], cwd=REPO, capture_output=True)
            assert ignored.returncode == 0, f"{probe} is not gitignored"
        kept = subprocess.run(
            ["git", "check-ignore", ".env.example"], cwd=REPO, capture_output=True, text=True
        )
        assert kept.returncode == 1, ".env.example is the documented template and must stay tracked"

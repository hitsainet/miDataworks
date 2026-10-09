"""T-10 in the Data-Juicer image: no package is installed while the worker runs.

py-data-juicer 1.6.0's lazy loader installs a missing dependency on first use and has no setting
to stop it, so the runner replaces the installer with a refusal and the image pins what the
offered operators import. These tests fail if either half goes missing.
"""

from __future__ import annotations

import sys

import pytest

import dj_paths

sys.path.insert(0, str(dj_paths.ROOT / "scripts"))

import check_no_runtime_installs  # noqa: E402


def test_running_every_offered_operator_installs_nothing() -> None:
    assert check_no_runtime_installs.run() == []


def test_the_lazy_installer_is_refused() -> None:
    from data_juicer.utils.lazy_loader import LazyLoader

    from src.operators.datajuicer import runner

    runner.forbid_runtime_installs()
    with pytest.raises(runner.RuntimeInstallRefused, match="definitely-not-installed"):
        LazyLoader._install_package("definitely-not-installed==1.0")


def test_a_missing_dependency_fails_loudly_instead_of_downloading() -> None:
    from data_juicer.utils.lazy_loader import LazyLoader

    from src.operators.datajuicer import runner

    runner.forbid_runtime_installs()
    missing = LazyLoader("dw_missing_module_xyz", "dw-missing-package-xyz")
    before = check_no_runtime_installs.installed()
    with pytest.raises(Exception) as exc:  # noqa: PT011 - the refusal may arrive wrapped
        missing.anything  # noqa: B018 - attribute access triggers the lazy import
    assert "dw-missing-package-xyz" in repr(exc.value) or "dw_missing_module_xyz" in repr(exc.value)
    assert check_no_runtime_installs.installed() == before


def test_every_pin_is_in_the_constraints() -> None:
    constraints = (dj_paths.ROOT / "constraints.txt").read_text().lower()
    for line in (dj_paths.ROOT / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            assert line.lower() in constraints, line


def test_running_an_operator_installs_the_refusal_itself() -> None:
    """The runner's apply() puts the refusal in place; a fresh interpreter proves it is not left
    over from another test."""
    import subprocess

    code = (
        "import sys, json; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "import dj_paths\n"
        "from contract import outcome\n"
        "outcome('text_length_filter', {'min_len': 20, 'max_len': 120})\n"
        "from data_juicer.utils.lazy_loader import LazyLoader\n"
        "from src.operators.datajuicer import runner\n"
        "print(LazyLoader._install_package.__func__ is runner._refuse_install)\n"
    ) % (str(dj_paths.ROOT), str(dj_paths.BACKEND))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert out.stdout.strip().splitlines()[-1] == "True", out.stderr[-2000:]


def test_the_check_reports_a_package_appearing_on_disk(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the site-packages comparison: a dist-info written while an operator
    runs is reported."""
    from pathlib import Path

    site_dir = Path(str(tmp_path))
    monkeypatch.setattr(check_no_runtime_installs.site, "getsitepackages", lambda: [str(site_dir)])
    monkeypatch.setattr(check_no_runtime_installs.site, "getusersitepackages", lambda: str(site_dir))

    def sneaky_install(op_name: str, params: dict[str, object]) -> dict[str, object]:
        (site_dir / f"sneaky_{op_name}-1.0.dist-info").mkdir(exist_ok=True)
        return {}

    monkeypatch.setattr(check_no_runtime_installs, "outcome", sneaky_install)
    problems = check_no_runtime_installs.run()
    assert problems and "installed set changed" in problems[-1]

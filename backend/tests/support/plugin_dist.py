"""A tiny, REAL installed distribution with a ``midataworks.operators`` entry point (FTID 003 section 8).

``importlib.metadata`` reads it from ``sys.path`` like any installed package. Its module writes a
marker file the moment it is imported, so a test can prove an entry point was listed WITHOUT being
imported (FR-003.11: importing runs code).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

MODULE_TEMPLATE = '''
"""A third-party operator package (test fixture)."""
from pathlib import Path

import pyarrow as pa

Path({marker!r}).write_text("imported")

from src.operators.manifest import OperatorManifest, ResourceSpec
from src.operators.protocol import OperatorResult


class Tagger:
    manifest = OperatorManifest(
        name="{op_name}",
        version="1",
        provider="plugin:{dist}",
        provider_version="{version}",
        kind="mapper",
        description="Adds a column saying a plugin touched the row.",
        params_schema={{"type": "object", "properties": {{}}, "additionalProperties": False}},
        resources=ResourceSpec(queue="curation"),
        deterministic=True,
    )

    def run(self, batch, params, ctx):
        return OperatorResult(
            output=batch.append_column("plugin_note", pa.array(["seen"] * batch.num_rows)),
            output_roles={{"plugin_note": "metadata"}},
        )


def operators():
    {body}
'''


def make_distribution(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dist: str = "acme-ops",
    version: str = "1.0",
    module: str = "acme_ops",
    entry: str = "tagger",
    op_name: str = "acme_tagger",
    broken: bool = False,
) -> Path:
    """Install the fixture distribution under ``root``; returns the marker file path."""
    marker = root / f"{module}.imported"
    package = root / module
    package.mkdir(parents=True)
    body = "raise RuntimeError('plugin exploded')" if broken else "return [Tagger()]"
    (package / "__init__.py").write_text(
        textwrap.dedent(
            MODULE_TEMPLATE.format(
                marker=str(marker), op_name=op_name, dist=dist, version=version, body=body
            )
        )
    )
    info = root / f"{module}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n")
    (info / "entry_points.txt").write_text(
        f"[midataworks.operators]\n{entry} = {module}:operators\n"
    )
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.delitem(sys.modules, module, raising=False)
    return marker

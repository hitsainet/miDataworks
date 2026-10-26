"""Every ``rule_set_identity`` case reproduces in another language from the file alone (D38-4).

The JavaScript below is RFC 8785's own reference construction (``JSON.stringify`` for scalars and
strings, keys sorted with the default ``Array.prototype.sort``, which compares UTF-16 code units). It
reads nothing but the contract file: it parses each case's ``document``, canonicalises it, and
compares the bytes with ``canonical_utf8`` and their SHA-256 with ``sha256``. A Python-only identity
(the pre-D38 ``json.dumps`` form) fails here on ``identity.number_forms`` and
``identity.key_order_utf16`` while still agreeing with itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from scripts.format_rule_signal_cases import DOCS_PATH

JCS_JS = r"""
const fs = require("fs");
const crypto = require("crypto");
function jcs(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(jcs).join(",") + "]";
  return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + jcs(v[k])).join(",") + "}";
}
const data = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const out = {};
for (const c of data.rule_set_identity) {
  const text = jcs(c.document);
  out[c.id] = {
    bytes: text === c.canonical_utf8,
    sha256: crypto.createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex") === c.sha256,
  };
}
process.stdout.write(JSON.stringify(out));
"""


def test_every_identity_case_reproduces_in_javascript() -> None:
    node = shutil.which("node")
    assert node, "node is needed for the other-language identity check (CI runners have it)"
    result = subprocess.run(
        [node, "-e", JCS_JS, str(DOCS_PATH)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    ids = [c["id"] for c in json.loads(DOCS_PATH.read_text(encoding="utf-8"))["rule_set_identity"]]
    assert sorted(found) == sorted(ids) and len(ids) >= 11
    assert {k: v for k, v in found.items() if v != {"bytes": True, "sha256": True}} == {}

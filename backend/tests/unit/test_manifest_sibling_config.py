"""The sibling configuration in the manifests (feature 020; FR-020.5, FR-020.13, FR-020.15,
FR-020.21, FR-020.27; FTASKS 4.13 at P0, 12.x/13.x/16.x later).

P0: every process that loads the backend's Settings takes the ConfigMap, so each of them reads the
same sibling names and records the same evidence (FR-020.5). The MCP server runs the backend image
but is its own deployment with no backend configuration (ADR-016); it must read no sibling name.

P2 (FTASKS 12.3; FTID §9.2, §9.3): the ConfigMap names miServer by its new variables and its new
in-cluster address, the old address survives only in the previous-address list, and every
Deployment that reads a changed key is rolled by a changed ``rebrand.hitsai/config-rev``
pod-template annotation (a ConfigMap change alone restarts nothing: ``envFrom`` is read at start).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core import env_names
from src.core.config import Settings
from src.core.key_binding import url_origin
from tests.support import legacy_identifiers as li
from tests.support.source_tree import mirror_snapshot

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "k8s" / "base"
BACKEND_IMAGE = "hitsai/midataworks-backend"
SIBLING_PREFIXES = ("MILLM_", "MISTUDIO_", "MISERVER_", "MIWORKBENCH_")
#: ADR-016: the MCP server reaches the backend over HTTP only and holds no backend configuration.
NOT_A_SETTINGS_PROCESS = {("midataworks-mcp", "mcp-server")}

CONFIGMAP = "midataworks-config"
#: The suite's one restart annotation (CF-27). Its value is the short SHA of the commit the change
#: is made on (a commit cannot name itself); only its CHANGE matters to ArgoCD.
CONFIG_REV = "rebrand.hitsai/config-rev"

#: The phase whose ConfigMap this tree carries. 13.3 (P3) moved it on; 16.2 (P4: miServer's admin
#: address is the public miServer host) moves it to P4; the P5 close (2026-11-12) to P5 and the
#: P6 close (2026-11-13) to P6, neither of which changes a ConfigMap key; the P7 close (2026-11-14) to
#: P7, after 020's P7 push removed the old keys (`a145f99`). It may run at most one
#: phase ahead of the export's ``closed`` and never behind it (checked below), so it cannot go
#: stale silently once the register closes a later phase.
CONFIG_PHASE = "P7"

#: In-cluster addresses, old and new (FTID §9.2). The old ones keep answering through an
#: ExternalName until P7.
MISERVER_NEW = "http://miserver-backend.miserver.svc.cluster.local:8000"
MISERVER_OLD = "http://millm-backend.millm.svc.cluster.local:8000"
MIWORKBENCH_NEW = "http://miworkbench-backend.miworkbench.svc.cluster.local:8000"
MIWORKBENCH_OLD = "http://mistudio-backend.mistudio.svc.cluster.local:8000"
#: The old services' hosts in any spelling a manifest could carry (short or fully qualified).
OLD_HOSTS = {
    "P2": re.compile(r"\bmillm-backend\.millm(\.svc(\.cluster\.local)?)?(?![\w.-])"),
    "P3": re.compile(r"\bmistudio-backend\.mistudio(\.svc(\.cluster\.local)?)?(?![\w.-])"),
}
#: 12.1 (2026-11-10): every miServer endpoint role is on the old in-cluster address; none on the
#: LAN alias ``k8s-millm.hitsai.local`` and no sibling role holds a key. So the previous list is
#: exactly the old in-cluster origin: an added origin widens what counts as the same miServer and
#: needs an operator decision (ID-9), recorded here.
MISERVER_PREVIOUS = [MISERVER_OLD]
MIWORKBENCH_PREVIOUS = [MIWORKBENCH_OLD]
PREVIOUS_KEYS = ("MISERVER_PREVIOUS_BASE_URLS", "MIWORKBENCH_PREVIOUS_BASE_URLS")
#: miServer's admin address as a browser reaches it, by the phase it took effect in (FR-020.21,
#: 16.2): the old public host from P2, the public miServer host from P4's admin-address commit.
UI_URLS = {"P2": "https://millm.hitsai.net", "P4": "https://miserver.hitsai.net"}
#: The old public host, in any value (P4 on, no ConfigMap value may still name it).
OLD_UI_HOST = re.compile(r"\bmillm\.hitsai\.net\b")


def expected_ui_url() -> str:
    """The value of the latest phase in force."""
    in_force = [phase for phase in UI_URLS if li.at_least(CONFIG_PHASE, phase)]
    assert in_force, f"no admin address is defined for {CONFIG_PHASE}"
    return UI_URLS[max(in_force, key=li.phase_index)]


def _at(phase: str) -> bool:
    return li.at_least(CONFIG_PHASE, phase)


def _docs(texts: dict[str, str]) -> list[dict[str, Any]]:
    return [d for text in texts.values() for d in yaml.safe_load_all(text) if d]


def _tree_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(BASE.glob("*.yaml"))}


def _configmap(docs: list[dict[str, Any]]) -> dict[str, str]:
    found = [
        d
        for d in docs
        if d.get("kind") == "ConfigMap" and d.get("metadata", {}).get("name") == CONFIGMAP
    ]
    assert len(found) == 1, f"expected one ConfigMap {CONFIGMAP}, found {len(found)}"
    return {str(k): str(v) for k, v in (found[0].get("data") or {}).items()}


def _template_of(doc: dict[str, Any]) -> dict[str, Any] | None:
    spec = doc.get("spec") or {}
    template = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get("template")
    return template or None


def _pod_templates() -> list[tuple[str, dict[str, Any]]]:
    found = []
    for doc in _docs(_tree_texts()):
        if "spec" not in doc:
            continue
        template = _template_of(doc)
        if template:
            found.append((doc["metadata"]["name"], template))
    return found


def _backend_containers() -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, container)
        for name, template in _pod_templates()
        for container in template["spec"].get("containers", [])
        if str(container.get("image", "")).startswith(BACKEND_IMAGE)
    ]


def _consumers(docs: list[dict[str, Any]], data: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Each workload that reads the ConfigMap: the keys it reads (``envFrom`` = every key,
    ``configMapKeyRef`` = its key) and its pod-template ``config-rev``. Derived from the manifests,
    never hand-kept (FTID §9.3)."""
    out: dict[str, dict[str, Any]] = {}
    for doc in docs:
        template = _template_of(doc)
        if not template:
            continue
        keys: set[str] = set()
        spec = template.get("spec") or {}
        for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
            for source in container.get("envFrom", []):
                if (source.get("configMapRef") or {}).get("name") == CONFIGMAP:
                    keys |= set(data) | {"*"}
            for entry in container.get("env", []):
                ref = (entry.get("valueFrom") or {}).get("configMapKeyRef") or {}
                if ref.get("name") == CONFIGMAP:
                    keys.add(str(ref["key"]))
        if keys:
            name = f"{doc['kind']}/{doc['metadata']['name']}"
            annotations = (template.get("metadata") or {}).get("annotations") or {}
            out[name] = {"keys": keys, "rev": annotations.get(CONFIG_REV)}
    return out


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def _base_commit() -> str:
    """The commit this tree's manifests are judged against. Uncommitted manifest edits are the
    commit being prepared, whose parent will be HEAD; otherwise HEAD is the commit under test and
    its parent is ``HEAD~1`` (CI checks out ``fetch-depth: 2``). The public mirror is a parentless
    snapshot and skips loudly; any other tree without a parent fails (a depth-1 clone would
    otherwise judge nothing)."""
    status = _git("status", "--porcelain", "--", "k8s/base")
    assert status.returncode == 0, f"git is required here: {status.stderr.strip()}"
    if status.stdout.strip():
        return "HEAD"
    parent = _git("rev-parse", "--verify", "--quiet", "HEAD~1^{commit}")
    if parent.returncode == 0:
        return parent.stdout.strip()
    if mirror_snapshot(REPO):
        pytest.skip(
            "SOURCE-ONLY: the public mirror is a parentless snapshot, so there is no previous "
            "ConfigMap to compare; the private CI judges this commit against its parent."
        )
    raise AssertionError(
        "HEAD has no parent in this checkout, so a ConfigMap change cannot be compared with the "
        "commit before it. Check out with fetch-depth: 2 (backend-tests.yml)."
    )


def _texts_at(commit: str) -> dict[str, str]:
    listed = _git("ls-tree", "--name-only", f"{commit}:k8s/base")
    assert listed.returncode == 0, listed.stderr
    texts = {}
    for name in listed.stdout.split():
        if name.endswith(".yaml"):
            shown = _git("show", f"{commit}:k8s/base/{name}")
            assert shown.returncode == 0, shown.stderr
            texts[name] = shown.stdout
    return texts


def _settings_from(data: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, Any]:
    """Settings as a pod built from this ConfigMap reads them: every sibling name cleared, then the
    ConfigMap's data set as the environment, resolved the way ``load_settings`` does."""
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    for key in PREVIOUS_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in data.items():
        monkeypatch.setenv(key, value)
    init, report = env_names.resolve(dict(os.environ))
    return Settings(**init), report  # type: ignore[arg-type]


# --- P0 ------------------------------------------------------------------------------------------


def test_every_backend_container_takes_the_configmap() -> None:
    containers = _backend_containers()
    settings_processes = [
        (owner, c) for owner, c in containers if (owner, c["name"]) not in NOT_A_SETTINGS_PROCESS
    ]
    assert len(settings_processes) >= 8, "the API, six workers and beat were not all found"
    for owner, container in settings_processes:
        sources = [e.get("configMapRef", {}).get("name") for e in container.get("envFrom", [])]
        assert "midataworks-config" in sources, f"{owner}/{container['name']} misses the ConfigMap"


def test_the_mcp_server_reads_no_sibling_name() -> None:
    containers = {(owner, c["name"]): c for owner, c in _backend_containers()}
    assert NOT_A_SETTINGS_PROCESS <= set(containers), "the exempt container no longer exists"
    for key in NOT_A_SETTINGS_PROCESS:
        container = containers[key]
        assert not container.get("envFrom"), key
        names = [e["name"] for e in container.get("env", [])]
        assert not [n for n in names if n.startswith(SIBLING_PREFIXES)], key


# --- P2 onward (FTASKS 12.3) ---------------------------------------------------------------------


def test_config_phase_follows_the_register() -> None:
    """The phase this ConfigMap is written for is the export's closed phase or the next one."""
    closed = li.load().header["closed"]
    floor = li.phase_index(closed) if closed is not None else -1
    assert floor <= li.phase_index(CONFIG_PHASE) <= floor + 1, (
        f"CONFIG_PHASE {CONFIG_PHASE} does not follow the register (closed: {closed}); move it on "
        "with the phase's ConfigMap commit"
    )


def test_configmap_sibling_keys_match_the_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _configmap(_docs(_tree_texts()))
    sibling = {k for k in data if k.startswith(SIBLING_PREFIXES)}
    # Every sibling key is one the backend reads: a misspelt key is silently ignored and the
    # sibling goes dark (standalone) instead of failing.
    read = {row.new for row in env_names.RENAMES} | {row.old for row in env_names.RENAMES}
    unread = sorted(sibling - read - set(PREVIOUS_KEYS))
    assert not unread, f"ConfigMap keys no Settings field reads: {unread}"
    settings, report = _settings_from(data, monkeypatch)
    if _at("P2"):
        stale = sorted(k for k in sibling if k.startswith("MILLM_"))
        assert not stale, f"{CONFIG_PHASE}: the ConfigMap still sets {stale}"
        for field in (
            "millm_base_url",
            "millm_ui_url",
            "millm_lease_ttl_seconds",
            "millm_lease_holder",
        ):
            assert report.read.get(field) == env_names.env_var_for(field), field
        assert settings.millm_base_url == MISERVER_NEW
        assert settings.millm_lease_ttl_seconds == 1800
        assert settings.millm_lease_holder == "midataworks"
    if _at("P3"):
        stale = sorted(k for k in sibling if k.startswith("MISTUDIO_"))
        assert not stale, f"{CONFIG_PHASE}: the ConfigMap still sets {stale}"
        assert report.read.get("mistudio_base_url") == "MIWORKBENCH_BASE_URL"
        assert settings.mistudio_base_url == MIWORKBENCH_NEW


def test_previous_lists_hold_the_old_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _configmap(_docs(_tree_texts()))
    settings, _ = _settings_from(data, monkeypatch)
    if _at("P2"):
        assert list(settings.miserver_previous_base_urls) == [
            url_origin(u) for u in MISERVER_PREVIOUS
        ], f"MISERVER_PREVIOUS_BASE_URLS={data.get('MISERVER_PREVIOUS_BASE_URLS')!r}"
        assert url_origin(settings.millm_base_url) not in settings.miserver_previous_base_urls
    if _at("P3"):
        assert list(settings.miworkbench_previous_base_urls) == [
            url_origin(u) for u in MIWORKBENCH_PREVIOUS
        ], f"MIWORKBENCH_PREVIOUS_BASE_URLS={data.get('MIWORKBENCH_PREVIOUS_BASE_URLS')!r}"


def test_ui_url_value_by_phase() -> None:
    data = _configmap(_docs(_tree_texts()))
    key = "MISERVER_UI_URL" if _at("P2") else "MILLM_UI_URL"
    assert data.get(key) == expected_ui_url(), (key, data.get(key))
    if _at("P4"):
        # FR-020.21: the Label step's Models link follows this value, so the old public host
        # must be gone from every ConfigMap value, not only from this key (control M62).
        assert expected_ui_url() == "https://miserver.hitsai.net"
        stale = {k: v for k, v in data.items() if OLD_UI_HOST.search(str(v))}
        assert not stale, f"P4: ConfigMap values still on the old public host: {stale}"


def test_the_admin_address_reaches_health_as_ui_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ConfigMap's value is the one Settings reads (by its NEW variable name) and the one
    ``/api/health`` serves as ``dependencies.millm.ui_url``, which the Label step links to."""
    data = _configmap(_docs(_tree_texts()))
    settings, report = _settings_from(data, monkeypatch)
    assert settings.millm_ui_url == expected_ui_url()
    assert report.read.get("millm_ui_url") == "MISERVER_UI_URL"


def test_old_incluster_address_only_in_previous_lists() -> None:
    """The old in-cluster addresses appear in no manifest value but the previous lists: a value
    left on the old address would keep working through the ExternalName and break at P7."""
    docs = _docs(_tree_texts())
    values: list[tuple[str, str]] = [(f"ConfigMap {k}", v) for k, v in _configmap(docs).items()]
    for name, template in _pod_templates():
        spec = template.get("spec") or {}
        for c in [*spec.get("initContainers", []), *spec.get("containers", [])]:
            for e in c.get("env", []):
                values.append((f"{name}/{c['name']} {e['name']}", str(e.get("value", ""))))
            for arg in [*c.get("command", []), *c.get("args", [])]:
                values.append((f"{name}/{c['name']} command", str(arg)))
    hosts = [rx for phase, rx in OLD_HOSTS.items() if _at(phase)]
    assert hosts, "no phase in force retires an in-cluster address"
    found = [
        (where, value)
        for where, value in values
        if any(rx.search(value) for rx in hosts)
        and not where.removeprefix("ConfigMap ").startswith(PREVIOUS_KEYS)
    ]
    assert not found, f"old in-cluster address outside the previous lists: {found}"


def test_configmap_change_bumps_every_consumer() -> None:
    """A changed key rolls every Deployment that reads it (FTID §9.3, ID-2, CF-27)."""
    base = _base_commit()
    now_docs = _docs(_tree_texts())
    before_docs = _docs(_texts_at(base))
    now_data, before_data = _configmap(now_docs), _configmap(before_docs)
    changed = {k for k in set(now_data) | set(before_data) if now_data.get(k) != before_data.get(k)}
    now, before = _consumers(now_docs, now_data), _consumers(before_docs, before_data)
    assert len(now) >= 3, f"expected the backend, designer and data-juicer, found {sorted(now)}"
    missed = []
    for name, consumer in sorted(now.items()):
        reads = (consumer["keys"] - {"*"}) | (changed if "*" in consumer["keys"] else set())
        hit = sorted(reads & changed)
        if not hit:
            continue
        rev, old_rev = consumer["rev"], before.get(name, {}).get("rev")
        # A string (an unquoted all-digit SHA would load as a number, which Kubernetes refuses).
        if not isinstance(rev, str) or rev == old_rev or not re.fullmatch(r"[0-9a-f]{7,40}", rev):
            missed.append(
                f"{name} reads changed {hit} but {CONFIG_REV} is {rev!r} (was {old_rev!r})"
            )
    assert not missed, "; ".join(missed)


def test_pd_s2_comment_names_miworkbench_denied() -> None:
    """020 13.3 (PD-S2): from P3 the comment above `midataworks-denied` names the sibling's Service as it is now,
    `miworkbench-denied`; the old name would send a reader to a Service that no longer exists."""
    text = (BASE / "backend.yaml").read_text(encoding="utf-8")
    head = text.split("name: midataworks-denied")[0].rsplit("---", 1)[-1]
    if _at("P3"):
        assert "`miworkbench-denied`" in head and "mistudio-denied" not in head, head
    else:
        assert "`mistudio-denied`" in head, head

"""The Kubernetes base and the ArgoCD definitions say what Foundation decided (ADR-025; 16.x, 17.1).

Read straight from ``k8s/`` (no cluster, no kubectl needed). Where a fact has an authority in
code, the expectation is DERIVED from it rather than restated: the queues from the live Celery
``QUEUES``, the required variables from ``REQUIRED_VARIABLES``, the emit and Socket.IO paths from
their modules, and the image names from ``docker-images.yml``. The gitops/pilot sync is RUN
against scratch repositories.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "k8s" / "base"
ARGOCD = REPO / "k8s" / "argocd" / "midataworks-app.yaml"
NODE = "mcs-lnxhost02"
SECRET_NAME = "midataworks-secrets"
SECRET_KEYS = {
    "POSTGRES_PASSWORD",
    "SECRET_KEY",
    "SETTINGS_ENCRYPTION_KEY",
    "DESIGNER_HANDOFF_KEY",
    "MCP_BEARER_TOKEN",
}
PIN_FILE = "k8s/base/.argocd-source-midataworks.yaml"
#: Decision 1.2 (2026-10-06): every host path, and nothing else.
HOST_PATHS = {
    "/data/midataworks/postgres",
    "/data/midataworks/redis",
    "/data/midataworks/data",
    "/mnt/hdd2/pv/midataworks",
}
APP_IMAGES = {
    "hitsai/midataworks-backend",
    "hitsai/midataworks-frontend",
    "hitsai/midataworks-datajuicer",
    "hitsai/midataworks-designer",
}
#: Upstream images, pinned by major version or exact tag.
UPSTREAM_IMAGES = {"postgres:15", "redis:7", "busybox:1.37"}


def kustomization() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    return data


def documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for name in kustomization()["resources"]:
        docs.extend(d for d in yaml.safe_load_all((BASE / name).read_text()) if d)
    return docs


def of_kind(kind: str) -> list[dict[str, Any]]:
    return [d for d in documents() if d["kind"] == kind]


def named(kind: str, name: str) -> dict[str, Any]:
    (doc,) = [d for d in of_kind(kind) if d["metadata"]["name"] == name]
    return doc


def pod_specs() -> Iterator[tuple[str, dict[str, Any]]]:
    for doc in documents():
        if doc["kind"] == "Deployment":
            yield doc["metadata"]["name"], doc["spec"]["template"]["spec"]
        elif doc["kind"] == "CronJob":
            yield doc["metadata"]["name"], doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def containers(spec: dict[str, Any], init: bool = True) -> list[dict[str, Any]]:
    out = list(spec.get("containers", []))
    return out + list(spec.get("initContainers", [])) if init else out


def env_of(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in container.get("env", [])}


def config_data() -> dict[str, str]:
    data: dict[str, str] = named("ConfigMap", "midataworks-config")["data"]
    return data


class TestTheBase:
    def test_every_manifest_is_listed_and_every_listed_file_exists(self) -> None:
        listed = set(kustomization()["resources"])
        on_disk = {p.name for p in BASE.glob("*.yaml")} - {"kustomization.yaml"}
        assert listed == on_disk

    def test_one_namespace(self) -> None:
        assert [d["metadata"]["name"] for d in of_kind("Namespace")] == ["midataworks"]
        for doc in documents():
            if doc["kind"] != "Namespace":
                assert doc["metadata"]["namespace"] == "midataworks", doc["metadata"]["name"]

    def test_images_are_the_app_images_or_pinned_upstream(self) -> None:
        assert {i["name"] for i in kustomization()["images"]} == APP_IMAGES
        for name, spec in pod_specs():
            for c in containers(spec):
                image = c["image"]
                assert image.split(":")[0] in APP_IMAGES or image in UPSTREAM_IMAGES, (name, image)

    def test_the_image_names_match_what_ci_builds(self) -> None:
        workflow = (REPO / ".github" / "workflows" / "docker-images.yml").read_text()
        for image in APP_IMAGES:
            assert f'echo "repo={image}"' in workflow or f"repo={image}" in workflow, image

    def test_the_base_renders_with_kustomize_when_available(self, tmp_path: Path) -> None:
        kubectl = shutil.which("kubectl")
        if not kubectl:
            pytest.skip("kubectl is not installed; the structural checks above still ran")
        result = subprocess.run(
            [kubectl, "kustomize", str(BASE)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        assert "kind: Deployment" in result.stdout


class TestStorageIsPinned:
    def test_every_pod_with_a_host_path_is_pinned_to_the_node(self) -> None:
        found = 0
        for name, spec in pod_specs():
            host_paths = [v for v in spec.get("volumes", []) if "hostPath" in v]
            if not host_paths:
                continue
            found += 1
            assert spec.get("nodeSelector") == {"kubernetes.io/hostname": NODE}, name
            for volume in host_paths:
                assert volume["hostPath"]["type"] == "DirectoryOrCreate", (name, volume)
                assert volume["hostPath"]["path"] in HOST_PATHS, (name, volume)
        assert found >= 4

    def test_every_decided_host_path_is_used(self) -> None:
        used = {
            v["hostPath"]["path"]
            for _, spec in pod_specs()
            for v in spec.get("volumes", [])
            if "hostPath" in v
        }
        assert used == HOST_PATHS

    def test_no_persistent_volume_claims(self) -> None:
        # Decision 1.2 chose hostPath everywhere, so a claim would be an unreviewed second scheme.
        assert not of_kind("PersistentVolumeClaim") and not of_kind("PersistentVolume")

    def test_the_data_volume_is_at_data_dataworks_on_every_row_touching_container(self) -> None:
        assert config_data()["DATA_DIR"] == "/data/dataworks"
        for deployment in ("midataworks-backend", "midataworks-datajuicer"):
            spec = named("Deployment", deployment)["spec"]["template"]["spec"]
            (volume,) = [v for v in spec["volumes"] if "hostPath" in v]
            assert volume["hostPath"]["path"] == "/data/midataworks/data"
            for c in containers(spec):
                mounts = {m["name"]: m["mountPath"] for m in c.get("volumeMounts", [])}
                assert mounts.get(volume["name"]) == "/data/dataworks", (deployment, c["name"])


class TestNoGpu:
    def test_no_gpu_request_or_visibility(self) -> None:
        for name, spec in pod_specs():
            for c in containers(spec):
                resources = c.get("resources", {})
                for section in ("requests", "limits"):
                    assert not any("gpu" in k for k in resources.get(section, {})), (
                        name,
                        c["name"],
                    )
                assert "NVIDIA_VISIBLE_DEVICES" not in env_of(c), (name, c["name"])


def backend_containers() -> list[dict[str, Any]]:
    return containers(named("Deployment", "midataworks-backend")["spec"]["template"]["spec"], False)


class TestQueues:
    def test_every_backend_queue_has_exactly_one_worker_container(self) -> None:
        from src.core.celery_app import QUEUES

        consumed: list[str] = []
        for c in backend_containers():
            env = env_of(c)
            if env.get("SERVICE_TYPE", {}).get("value") == "worker":
                consumed.extend(q.strip() for q in env["CELERY_QUEUES"]["value"].split(","))
        assert sorted(consumed) == sorted(set(consumed)), f"a queue has two consumers: {consumed}"
        assert set(consumed) == set(QUEUES) - {"datajuicer", "datajuicer_preview", "designer"}

    def test_the_datajuicer_image_consumes_its_queue_and_nothing_else_does(self) -> None:
        from src.core.celery_app import QUEUES

        dockerfile = (REPO / "datajuicer" / "Dockerfile").read_text()
        match = re.search(r'"-Q",\s*"([^"]+)"', dockerfile)
        assert match, "datajuicer/Dockerfile names no queue"
        dj_queues = set(match.group(1).split(","))
        assert dj_queues == {"datajuicer", "datajuicer_preview"}
        assert re.search(
            r'"--concurrency",\s*"2"', dockerfile
        ), "a preview must not wait behind a step"
        dj = named("Deployment", "midataworks-datajuicer")["spec"]["template"]["spec"]
        assert "command" not in dj["containers"][0] and "args" not in dj["containers"][0]
        # Feature 003 added the preview task and its queue to the image: nothing is unconsumed.
        unconsumed = (
            set(QUEUES)
            - dj_queues
            - {"designer"}  # the Data Designer image's, checked below
            - {
                q.strip()
                for c in backend_containers()
                if "CELERY_QUEUES" in env_of(c)
                for q in env_of(c)["CELERY_QUEUES"]["value"].split(",")
            }
        )
        assert unconsumed == set()

    def test_the_designer_image_consumes_its_queue_and_holds_only_the_handoff_key(self) -> None:
        """ADR-010 amendment (2026-10-07): the Data-Juicer pattern for Data Designer."""
        dockerfile = (REPO / "designer" / "Dockerfile").read_text()
        match = re.search(r'"-Q",\s*"([^"]+)"', dockerfile)
        assert match and set(match.group(1).split(",")) == {"designer"}
        assert re.search(r'"--concurrency",\s*"2"', dockerfile)
        spec = named("Deployment", "midataworks-designer")["spec"]["template"]["spec"]
        assert spec["nodeSelector"] == {"kubernetes.io/hostname": "mcs-lnxhost02"}
        (container,) = spec["containers"]
        assert "command" not in container and "args" not in container
        env = env_of(container)
        assert set(env) == {"REDIS_URL", "DATA_DIR", "DESIGNER_HANDOFF_KEY"}
        secrets = [e for e in env.values() if "secretKeyRef" in str(e)]
        assert [s["valueFrom"]["secretKeyRef"]["key"] for s in secrets] == ["DESIGNER_HANDOFF_KEY"]
        assert "envFrom" not in container
        assert "data_designer" not in (REPO / "backend" / "requirements.txt").read_text()

    def test_the_datajuicer_deployment_holds_no_database_or_secret(self) -> None:
        """ADR-010, FTASKS 13.2: the runner never touches PostgreSQL; its pod cannot."""
        dj = named("Deployment", "midataworks-datajuicer")["spec"]["template"]["spec"]
        for container in dj["containers"]:
            env = env_of(container)
            assert "DATABASE_URL" not in env
            assert not any("secretKeyRef" in str(v) for v in env.values())
            assert "envFrom" not in container

    def test_one_api_and_one_beat(self) -> None:
        types = [env_of(c)["SERVICE_TYPE"]["value"] for c in backend_containers()]
        assert types.count("api") == 1 and types.count("beat") == 1
        assert set(types) <= {"api", "worker", "beat"}

    def test_the_backend_rolls_with_recreate(self) -> None:
        # Two Beat processes during a rolling update would double-schedule the janitor.
        assert named("Deployment", "midataworks-backend")["spec"]["strategy"]["type"] == "Recreate"


class TestSecrets:
    def test_every_secret_reference_names_the_one_secret_and_a_known_key(self) -> None:
        for name, spec in pod_specs():
            for c in containers(spec):
                for e in c.get("env", []):
                    ref = e.get("valueFrom", {}).get("secretKeyRef")
                    if ref:
                        assert ref["name"] == SECRET_NAME, (name, e)
                        assert ref["key"] in SECRET_KEYS, (name, e)

    def test_each_backend_container_has_every_required_variable(self) -> None:
        from src.core.config import REQUIRED_VARIABLES

        for c in backend_containers():
            available = set(env_of(c))
            for source in c.get("envFrom", []):
                if source.get("configMapRef", {}).get("name") == "midataworks-config":
                    available |= set(config_data())
            missing = set(REQUIRED_VARIABLES) - available
            assert not missing, (c["name"], missing)

    def test_database_url_is_composed_after_the_password_is_defined(self) -> None:
        for c in backend_containers():
            names = [e["name"] for e in c["env"]]
            assert names.index("POSTGRES_PASSWORD") < names.index("DATABASE_URL"), c["name"]
            url = env_of(c)["DATABASE_URL"]["value"]
            assert url == (
                "postgresql+asyncpg://midataworks:$(POSTGRES_PASSWORD)@postgres:5432/midataworks"
            )
            assert env_of(c)["INTERNAL_API_SECRET"]["valueFrom"]["secretKeyRef"]["key"] == (
                "SECRET_KEY"
            )

    def test_the_datajuicer_worker_holds_no_credentials(self) -> None:
        spec = named("Deployment", "midataworks-datajuicer")["spec"]["template"]["spec"]
        for c in containers(spec):
            for e in c.get("env", []):
                assert "secretKeyRef" not in e.get("valueFrom", {}), e
                assert e["name"] not in {"DATABASE_URL", "POSTGRES_PASSWORD"}, e
            assert "envFrom" not in c

    def test_no_literal_secret_value_is_committed(self) -> None:
        for name, spec in pod_specs():
            for c in containers(spec):
                for e in c.get("env", []):
                    if re.search(r"PASSWORD|SECRET|_KEY$", e["name"]) and "value" in e:
                        pytest.fail(f"{name}/{c['name']} sets {e['name']} to a literal value")
        for key in config_data():
            assert not re.search(r"PASSWORD|SECRET|KEY", key), key

    def test_the_secret_command_is_documented_without_a_value(self) -> None:
        text = (BASE / "postgres.yaml").read_text()
        assert "kubectl create secret generic midataworks-secrets -n midataworks" in text
        for key in SECRET_KEYS:
            assert re.search(rf'--from-literal={key}="\$\(openssl rand -hex \d+\)"', text), key

    def test_app_containers_run_as_non_root(self) -> None:
        for deployment in ("midataworks-backend", "midataworks-datajuicer"):
            spec = named("Deployment", deployment)["spec"]["template"]["spec"]
            assert spec["securityContext"]["runAsNonRoot"] is True
            assert spec["securityContext"]["runAsUser"] == 10001
            for c in containers(spec, init=False):
                assert "runAsUser" not in c.get("securityContext", {}), c["name"]


#: The LAN name and the public name the Cloudflare tunnel forwards (the miLLM/miStudio pattern).
HOSTS = ("k8s-midataworks.hitsai.local", "k8s-midataworks.hitsai.net", "midataworks.hitsai.net")

#: The MCP Ingress (feature 010). It was LAN-only until the operator decided on 2026-10-07 to reach
#: it off the LAN: mcp-dataworks.hitsai.net is the public name, and it must carry exactly the LAN
#: name's routes. The app's public names (HOSTS) must never route to the MCP service.
MCP_INGRESS = "midataworks-mcp-ingress"
MCP_HOSTS = (
    "k8s-midataworks.hitsai.local",
    "mcp-dataworks.hitsai.local",
    "mcp-dataworks.hitsai.net",
)


class TestIngress:
    def test_every_ingress_serves_both_hosts_with_identical_paths(self) -> None:
        """The public host must get exactly the LAN host's routes — above all the /internal
        denial — so a tunnel can never reach a path the LAN host refuses."""
        for ingress in of_kind("Ingress"):
            if ingress["metadata"]["name"] == MCP_INGRESS:
                continue
            by_host = {r["host"]: r["http"]["paths"] for r in ingress["spec"]["rules"]}
            assert set(by_host) == set(HOSTS), ingress["metadata"]["name"]
            for host in HOSTS[1:]:
                assert by_host[host] == by_host[HOSTS[0]], (ingress["metadata"]["name"], host)

    def test_the_mcp_ingress_serves_its_hosts_and_only_the_mcp_service(self) -> None:
        mcp = named("Ingress", MCP_INGRESS)
        by_host = {r["host"]: r["http"]["paths"] for r in mcp["spec"]["rules"]}
        assert set(by_host) == set(MCP_HOSTS), set(by_host)
        # The public name gets exactly the dedicated LAN name's routes, nothing more.
        assert by_host["mcp-dataworks.hitsai.net"] == by_host["mcp-dataworks.hitsai.local"]
        for rule in mcp["spec"]["rules"]:
            for p in rule["http"]["paths"]:
                assert p["backend"]["service"]["name"] == "midataworks-mcp"

    def paths(self) -> list[tuple[str, str]]:
        out = []
        for ingress in of_kind("Ingress"):
            allowed = MCP_HOSTS if ingress["metadata"]["name"] == MCP_INGRESS else HOSTS
            for rule in ingress["spec"]["rules"]:
                assert rule["host"] in allowed
                for p in rule["http"]["paths"]:
                    out.append((p["path"], p["backend"]["service"]["name"]))
        return out

    def test_the_backend_is_reached_only_under_api(self) -> None:
        for path, service in self.paths():
            if service == "midataworks-backend":
                assert path == "/api" or path.startswith("/api/"), path

    def test_the_internal_emit_route_is_routed_at_a_service_with_no_pods(self) -> None:
        from src.workers.emit import INTERNAL_EMIT_PATH

        routes = dict(self.paths())
        prefix = "/" + INTERNAL_EMIT_PATH.strip("/").split("/")[0]
        assert routes.get(prefix) == "midataworks-denied"
        denied = named("Service", "midataworks-denied")["spec"]["selector"]
        labels = [d["spec"]["template"]["metadata"]["labels"] for d in of_kind("Deployment")]
        assert all(not (denied.items() <= lab.items()) for lab in labels)

    def test_the_socket_path_reaches_the_backend(self) -> None:
        from src.core.websocket import SOCKETIO_PATH

        backend_prefixes = [p for p, s in self.paths() if s == "midataworks-backend"]
        assert any(SOCKETIO_PATH.startswith(p.rstrip("/") + "/") for p in backend_prefixes)

    def test_service_ports_match_container_ports(self) -> None:
        frontend = named("Service", "midataworks-frontend")["spec"]["ports"][0]
        assert frontend == {"port": 80, "targetPort": 8080}
        backend = named("Service", "midataworks-backend")["spec"]["ports"][0]
        assert backend["targetPort"] == 8000


def argocd_documents() -> dict[str, dict[str, Any]]:
    return {d["kind"]: d for d in yaml.safe_load_all(ARGOCD.read_text()) if d}


class TestArgoCD:
    def test_the_three_resources(self) -> None:
        assert set(argocd_documents()) == {"AppProject", "Application", "ImageUpdater"}

    def test_the_application(self) -> None:
        app = argocd_documents()["Application"]
        assert app["metadata"]["name"] == "midataworks"
        spec = app["spec"]
        assert spec["project"] == "midataworks"
        assert spec["source"] == {
            "repoURL": "https://github.com/Onegaishimas/miDataworks.git",
            "targetRevision": "gitops/pilot",
            "path": "k8s/base",
        }
        assert spec["destination"]["namespace"] == "midataworks"
        assert spec["syncPolicy"]["automated"]["selfHeal"] is True
        assert "ServerSideApply=true" in spec["syncPolicy"]["syncOptions"]

    def test_the_project_admits_the_repository_and_namespace(self) -> None:
        project = argocd_documents()["AppProject"]
        app = argocd_documents()["Application"]
        assert project["metadata"]["name"] == "midataworks"
        assert app["spec"]["source"]["repoURL"] in project["spec"]["sourceRepos"]
        assert {"namespace": "midataworks", "server": "https://kubernetes.default.svc"} in (
            project["spec"]["destinations"]
        )

    def test_image_updater_pins_every_app_image_by_digest_to_gitops_pilot(self) -> None:
        annotations = argocd_documents()["Application"]["metadata"]["annotations"]
        prefix = "argocd-image-updater.argoproj.io/"
        aliases = {}
        for entry in annotations[prefix + "image-list"].split(","):
            alias, ref = entry.strip().split("=")
            aliases[alias] = ref
            assert annotations[f"{prefix}{alias}.update-strategy"] == "digest"
            image = annotations[f"{prefix}{alias}.kustomize.image-name"]
            assert ref == f"docker.io/{image}:latest"
        assert {annotations[f"{prefix}{a}.kustomize.image-name"] for a in aliases} == APP_IMAGES
        assert annotations[prefix + "write-back-method"] == "git"
        assert annotations[prefix + "git-branch"] == "gitops/pilot"
        updater = argocd_documents()["ImageUpdater"]["spec"]
        assert updater["applicationRefs"] == [
            {"namePattern": "midataworks", "useAnnotations": True}
        ]
        assert updater["writeBackConfig"]["gitConfig"]["branch"] == "gitops/pilot"

    def test_the_repository_credential_is_documented(self) -> None:
        text = ARGOCD.read_text()
        assert "midataworks-repo-creds" in text
        assert "argocd.argoproj.io/secret-type: repository" in text


# --- the gitops/pilot sync, run for real ------------------------------------------------------


def gitops_workflow() -> dict[str, Any]:
    data = yaml.safe_load((REPO / ".github/workflows/sync-main-to-gitops-pilot.yml").read_text())
    if True in data:
        data["on"] = data.pop(True)
    job: dict[str, Any] = data["jobs"]["sync"]
    return job


def gitops_step(name: str) -> str:
    for s in gitops_workflow()["steps"]:
        if s.get("name") == name:
            return str(s["run"])
    raise AssertionError(name)


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class TestGitopsSync:
    def test_the_pin_file_is_the_image_updater_default_and_not_on_main(self) -> None:
        assert gitops_workflow()["env"]["PIN_FILE"] == PIN_FILE
        assert Path(PIN_FILE).name == ".argocd-source-midataworks.yaml"  # .argocd-source-<app>
        assert PIN_FILE in ARGOCD.read_text()
        tracked = subprocess.run(
            ["git", "ls-files", PIN_FILE], cwd=REPO, capture_output=True, text=True
        ).stdout
        assert not tracked.strip(), "the pin file must never be committed on main"

    def test_runs_only_in_the_private_repository(self) -> None:
        assert "Onegaishimas/miDataworks" in gitops_workflow()["if"]

    def test_the_sync_keeps_the_pin_and_drops_drift(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin.git"
        sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        work = tmp_path / "work"
        sh(tmp_path, "clone", "-q", str(origin), str(work))
        (work / "k8s/base").mkdir(parents=True)
        (work / "k8s/base/backend.yaml").write_text("v1\n")
        sh(work, "add", "-A")
        sh(work, "commit", "-q", "-m", "main 1")
        sh(work, "push", "-q", "origin", "HEAD:main")
        # gitops/pilot: Image Updater's pin, plus drift that must not survive.
        sh(work, "checkout", "-q", "-b", "gitops/pilot")
        (work / PIN_FILE).write_text("images: pinned@sha256:abc\n")
        (work / "k8s/base/backend.yaml").write_text("hand edit on pilot\n")
        (work / "pilot-only.txt").write_text("drift\n")
        sh(work, "add", "-A")
        sh(work, "commit", "-q", "-m", "pin and drift")
        sh(work, "push", "-q", "origin", "gitops/pilot")
        # main moves on.
        sh(work, "checkout", "-q", "main")
        (work / "k8s/base/backend.yaml").write_text("v2\n")
        (work / "k8s/base/new.yaml").write_text("new\n")
        sh(work, "add", "-A")
        sh(work, "commit", "-q", "-m", "main 2")
        sh(work, "push", "-q", "origin", "main")

        runner = tmp_path / "runner"
        sh(tmp_path, "clone", "-q", "-b", "gitops/pilot", str(origin), str(runner))
        output = tmp_path / "out"
        output.write_text("")
        env = {**os.environ, "PIN_FILE": PIN_FILE, "GITHUB_OUTPUT": str(output)}
        for name in (
            "Configure git",
            "Skip if gitops/pilot already contains main",
            "Merge main with -X theirs (main is canonical for non-pin files)",
            "Converge non-pin files to main (kill gitops/pilot-only drift)",
        ):
            result = subprocess.run(
                ["bash", "-e", "-c", gitops_step(name)],
                cwd=runner,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, (name, result.stdout, result.stderr)
        assert "skip=false" in output.read_text()
        assert (runner / PIN_FILE).read_text() == "images: pinned@sha256:abc\n"
        assert (runner / "k8s/base/backend.yaml").read_text() == "v2\n"
        assert (runner / "k8s/base/new.yaml").exists()
        assert not (runner / "pilot-only.txt").exists(), "gitops/pilot-only drift survived"
        assert sh(runner, "status", "--porcelain").strip() == ""

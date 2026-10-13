"""The send plan, registration bodies and the approval digest (FR-009.4, FR-009.23, FR-009.26,
FR-009.82; FTDD 009 sections 5.2, 5.4; FTID 009 section 3).

Pure: the service injects every fact (publish requests and their 008 digests, expected counts,
capabilities). There is exactly ONE function that builds a registration body
(:func:`registration_body`) and it builds from an explicit key list, so the body the approval
digest covers, the body stored on the step and the body sent to miStudio cannot be three different
serialisations (FTDD 009 section 12, "two serialisations of a registration body").

Approval digest (FR-009.82)::

    sha256(canonical_json({action: "hub_push", set_id, snapshot_sha256,
                           publish_digests: sorted, registration_bodies: sorted,
                           mistudio_base_url}))

Deviation (recorded in the controls review): miStudio's ``dataset_id`` for a registration is only
known after the download, so the bodies the digest covers are TEMPLATES with ``dataset_id: null``.
Every other field — name, columns, mapping, role, distribution, pair column — is bound.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...core.canonical_json import canonical_json, canonical_sha256
from .role_mapping import mistudio_role

#: Keys every registration body carries (miStudio ``ProbeDatasetCreate``; FR-009.23). ``distribution``
#: is added only for an ``eval`` role, ``pair_column`` only when set, ``dataset_version_manifest`` only
#: when miStudio serves the field (XR-1). ``keyword_filter`` is NEVER sent (FR-009.23).
REGISTRATION_KEYS: tuple[str, ...] = (
    "name",
    "dataset_id",
    "config",
    "split",
    "input_column",
    "label_column",
    "label_mapping",
    "role",
)
OPTIONAL_KEYS: tuple[str, ...] = ("distribution", "pair_column", "dataset_version_manifest")
ALL_KEYS: frozenset[str] = frozenset(REGISTRATION_KEYS + OPTIONAL_KEYS)


class RegistrationBodyError(ValueError):
    """A body carries a key outside the pinned set (FTDD 009 section 8)."""


def registration_body(
    *,
    name: str,
    dataset_id: str | None,
    config: str | None,
    split: str,
    input_column: str,
    label_column: str,
    label_mapping: Mapping[str, str],
    role: str,
    pair_column: str | None,
    manifest: Mapping[str, Any] | None = None,
    manifest_served: bool = False,
) -> dict[str, Any]:
    """The exact miStudio ``POST /probe-monitors/datasets`` body for one role."""
    mistudio, distribution = mistudio_role(role)
    body: dict[str, Any] = {
        "name": name,
        "dataset_id": dataset_id,
        "config": config,
        "split": split,
        "input_column": input_column,
        "label_column": label_column,
        "label_mapping": {str(k): str(v) for k, v in sorted(label_mapping.items())},
        "role": mistudio,
    }
    if distribution is not None:
        body["distribution"] = distribution
    if pair_column:
        body["pair_column"] = pair_column
    if manifest_served and manifest is not None:
        body["dataset_version_manifest"] = dict(manifest)
    check_body_keys(body)
    return body


def check_body_keys(body: Mapping[str, Any]) -> None:
    """Refuse a body holding any key outside the pinned set (FTDD 009 section 8)."""
    extra = set(body) - ALL_KEYS
    if extra:
        raise RegistrationBodyError(f"registration body carries unpinned keys {sorted(extra)}")


@dataclass(frozen=True)
class PublishUnit:
    version_id: str
    repo_id: str
    label_column: str | None
    visibility: str
    #: A completed 008 build of this version (None when an existing publish is reused).
    build_id: str | None
    card_prose: str
    #: 008's ``publish_request_digest`` for the request (None when reused).
    digest: str | None
    #: An existing verified publish of this version to this repository (FR-009.19).
    reuse_publish_id: str | None = None


@dataclass(frozen=True)
class DownloadUnit:
    repo_id: str
    config: str | None
    split: str
    version_id: str
    role_ids: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.repo_id}|{self.config or ''}|{self.split}"


@dataclass(frozen=True)
class RoleUnit:
    role_id: str
    role: str
    version_id: str
    split: str
    download_key: str
    body: dict[str, Any]
    expected_counts: dict[str, int]
    mapping_sha256: str
    columns_sha256: str


@dataclass(frozen=True)
class SendPlan:
    publish_units: tuple[PublishUnit, ...]
    download_units: tuple[DownloadUnit, ...]
    roles: tuple[RoleUnit, ...]
    mistudio_base_url: str
    manifest_served: bool
    capabilities: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mistudio_base_url": self.mistudio_base_url,
            "manifest_served": self.manifest_served,
            "capabilities": dict(self.capabilities),
            "publish_units": [
                {
                    "version_id": u.version_id,
                    "repo_id": u.repo_id,
                    "label_column": u.label_column,
                    "visibility": u.visibility,
                    "build_id": u.build_id,
                    "card_prose": u.card_prose,
                    "digest": u.digest,
                    "reuse_publish_id": u.reuse_publish_id,
                }
                for u in self.publish_units
            ],
            "download_units": [
                {
                    "key": u.key,
                    "repo_id": u.repo_id,
                    "config": u.config,
                    "split": u.split,
                    "version_id": u.version_id,
                    "role_ids": list(u.role_ids),
                }
                for u in self.download_units
            ],
            "roles": [
                {
                    "role_id": r.role_id,
                    "role": r.role,
                    "version_id": r.version_id,
                    "split": r.split,
                    "download_key": r.download_key,
                    "body": r.body,
                    "expected_counts": r.expected_counts,
                    "mapping_sha256": r.mapping_sha256,
                    "columns_sha256": r.columns_sha256,
                }
                for r in self.roles
            ],
        }


@dataclass(frozen=True)
class RoleInput:
    """One role of the snapshot, with its repository and expected counts."""

    role_id: str
    role: str
    version_id: str
    split: str
    input_column: str
    label_column: str
    label_mapping: Mapping[str, str]
    pair_column: str | None
    view_name: str
    expected_counts: Mapping[str, int]


def mapping_sha256(mapping: Mapping[str, str]) -> str:
    return canonical_sha256({str(k): str(v) for k, v in mapping.items()})


def columns_sha256(input_column: str, label_column: str, pair_column: str | None) -> str:
    return canonical_sha256(
        {"input_column": input_column, "label_column": label_column, "pair_column": pair_column}
    )


def build_plan(
    roles: Sequence[RoleInput],
    publish_units: Sequence[PublishUnit],
    *,
    mistudio_base_url: str,
    manifest_served: bool,
    capabilities: Mapping[str, Any] | None = None,
) -> SendPlan:
    """Group roles into download units by (repository, split) and build every body template."""
    repo_by_version = {u.version_id: u.repo_id for u in publish_units}
    downloads: dict[str, list[str]] = {}
    unit_meta: dict[str, tuple[str, str, str]] = {}
    role_units: list[RoleUnit] = []
    for r in sorted(roles, key=lambda x: (x.role, x.role_id)):
        repo = repo_by_version[r.version_id]
        key = f"{repo}||{r.split}"
        downloads.setdefault(key, []).append(r.role_id)
        unit_meta[key] = (repo, r.split, r.version_id)
        body = registration_body(
            name=r.view_name,
            dataset_id=None,
            config=None,
            split=r.split,
            input_column=r.input_column,
            label_column=r.label_column,
            label_mapping=r.label_mapping,
            role=r.role,
            pair_column=r.pair_column,
            manifest=None,
            manifest_served=False,
        )
        role_units.append(
            RoleUnit(
                role_id=r.role_id,
                role=r.role,
                version_id=r.version_id,
                split=r.split,
                download_key=key,
                body=body,
                expected_counts=dict(r.expected_counts),
                mapping_sha256=mapping_sha256(r.label_mapping),
                columns_sha256=columns_sha256(r.input_column, r.label_column, r.pair_column),
            )
        )
    download_units = tuple(
        DownloadUnit(
            repo_id=unit_meta[k][0],
            config=None,
            split=unit_meta[k][1],
            version_id=unit_meta[k][2],
            role_ids=tuple(sorted(ids)),
        )
        for k, ids in sorted(downloads.items())
    )
    return SendPlan(
        publish_units=tuple(sorted(publish_units, key=lambda u: u.version_id)),
        download_units=download_units,
        roles=tuple(role_units),
        mistudio_base_url=mistudio_base_url,
        manifest_served=manifest_served,
        capabilities=dict(capabilities or {}),
    )


def approval_digest(plan: Mapping[str, Any], *, set_id: str, snapshot_sha256: str) -> str:
    """FR-009.82: what one ``hub_push`` approval of a whole send binds to (P-06)."""
    publish_digests = sorted(
        str(u["digest"]) for u in plan["publish_units"] if u.get("digest") is not None
    )
    reused = sorted(
        str(u["reuse_publish_id"]) for u in plan["publish_units"] if u.get("reuse_publish_id")
    )
    bodies = sorted(canonical_json(r["body"]).decode("utf-8") for r in plan["roles"])
    return canonical_sha256(
        {
            "action": "hub_push",
            "set_id": set_id,
            "snapshot_sha256": snapshot_sha256,
            "publish_digests": publish_digests,
            "reused_publishes": reused,
            "registration_bodies": bodies,
            "mistudio_base_url": plan["mistudio_base_url"],
        }
    )

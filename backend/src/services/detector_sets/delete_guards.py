"""Feature 009's answers to 002's version delete (002 FR-002.37; ``REFERENCE_CHECKERS``).

A version bound to a role of a detector set is refused (``version_in_detector_set``, FR-009.13):
deleting it would leave a set whose send can no longer be rebuilt. Registrations, length profiles
and agreement reports are evidence kept with the tombstone (P-15), never a reason to refuse.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.detector_set import DetectorSet, DetectorSetRole
from ..version_delete_service import Reference, ReferenceChecker, register_reference_checker


async def version_in_detector_set(db: AsyncSession, version_id: str) -> Reference | None:
    """The membership query 002's delete refusal reads (FTID 009 section 2.2)."""
    found = (
        await db.execute(
            select(DetectorSet.id, DetectorSet.name, DetectorSetRole.role)
            .join(DetectorSetRole, DetectorSetRole.set_id == DetectorSet.id)
            .where(DetectorSetRole.version_id == version_id)
            .order_by(DetectorSet.name)
            .limit(1)
        )
    ).first()
    if found is None:
        return None
    set_id, name, role = found
    return Reference(
        "version_in_detector_set",
        f"Detector set {name!r} binds this version as its {role} role. Rebind that role to "
        "another version, then delete.",
        {"set_id": set_id, "role": role},
    )


async def version_in_running_chain(db: AsyncSession, version_id: str) -> Reference | None:
    """A RUNNING minimal-pair chain still reads its seed version and its scope version (its next
    stages build from them); a finished or stopped chain keeps them as evidence (P-15)."""
    from ...models.minimal_pair_chain import MinimalPairChain

    found = (
        await db.execute(
            select(MinimalPairChain.id)
            .where(
                MinimalPairChain.state == "running",
                (MinimalPairChain.input_version_id == version_id)
                | (MinimalPairChain.scope_version_id == version_id),
            )
            .limit(1)
        )
    ).first()
    if found is None:
        return None
    return Reference(
        "version_in_use",
        f"Minimal-pair chain {found[0]} is still building from this version. Wait for it, or "
        "cancel it, then delete.",
        {"chain_id": found[0]},
    )


async def version_in_unused_link(db: AsyncSession, version_id: str) -> Reference | None:
    """A reproduction link no run has used yet still points the gate at this version's rows: the
    operator deletes the link first. A USED link is evidence kept with the tombstone (P-15), and the
    gate skips a link whose version is deleted, so it never refuses here."""
    from ...models.label_run import LabelRun
    from ...models.reproduction_link import ReproductionLink

    used = select(LabelRun.id).where(
        LabelRun.endpoint_snapshot["reproduction"]["link_id"].astext == ReproductionLink.id
    )
    found = (
        await db.execute(
            select(ReproductionLink.id)
            .where(ReproductionLink.version_id == version_id, ~used.exists())
            .limit(1)
        )
    ).first()
    if found is None:
        return None
    return Reference(
        "version_in_use",
        f"Reproduction link {found[0]} makes this version's rows a reproduction gate target. Delete "
        "the link (no run has used it), then delete the version.",
        {"link_id": found[0]},
    )


async def _never(db: AsyncSession, version_id: str) -> Reference | None:
    """Kept with the tombstone (P-15): evidence, never a reason to refuse."""
    return None


register_reference_checker(
    ReferenceChecker("009", "dw_detector_set_roles", version_in_detector_set)
)
register_reference_checker(
    ReferenceChecker("009", "dw_minimal_pair_chains", version_in_running_chain)
)
register_reference_checker(ReferenceChecker("009", "dw_reproduction_links", version_in_unused_link))
for _table in ("dw_length_profiles", "dw_mistudio_registrations", "dw_agreement_reports"):
    register_reference_checker(ReferenceChecker("009", _table, _never))

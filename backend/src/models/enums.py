"""The single source of feature 002's vocabularies (task 3.1; FTDD 002 section 4.1).

Every CHECK constraint, the ``GET /datasets/meta`` response and the frontend's choices derive from
these classes; ``tests/unit/test_enums_single_source.py`` compares the live endpoint with them.

Stored as CHECK-constrained strings, not native PostgreSQL enum types. FTDD 002 section 4.1 names
native enums; Foundation's ``dw_jobs`` chose CHECK strings because extending a native enum needs a
non-transactional ``ALTER TYPE`` (miStudio worked around it on three tables), and Foundation's
code wins over the design text (recorded in the implementation-controls review).
"""

from __future__ import annotations

from enum import StrEnum


class TargetType(StrEnum):
    """The Goal step's choices (FR-002.1), plus ``untyped`` before a goal is chosen."""

    SFT = "sft"
    DPO = "dpo"
    KTO = "kto"
    GRPO_PROMPT = "grpo_prompt"
    PRM = "prm"
    DETECTOR = "detector"
    UNTYPED = "untyped"


class VersionState(StrEnum):
    """A version row exists only after a build succeeds (FTDD 002 section 4.2)."""

    COMPLETED = "completed"
    DELETED = "deleted"


class InputKind(StrEnum):
    SOURCE = "source"
    VERSION = "version"


class StepKind(StrEnum):
    ASSEMBLE = "assemble"
    OPERATOR = "operator"


class StepState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    """The four row-event kinds (FR-002.24, C-002.5)."""

    DROPPED = "dropped"
    CHANGED = "changed"
    ADDED = "added"
    SPLIT_ASSIGNED = "split_assigned"


class ColumnRole(StrEnum):
    """Each column's role in a version (FR-002.20, FR-002.23)."""

    CONTENT = "content"
    METADATA = "metadata"
    SYSTEM = "system"


class Origin(StrEnum):
    """``_dw_origin`` values (FR-002.23)."""

    SOURCE = "source"
    GENERATED = "generated"


class BindingKind(StrEnum):
    """Model-output runs a build may bind (FR-002.2, C-002.8)."""

    LABEL_RUN = "label_run"
    GENERATION_RUN = "generation_run"


class VerificationResult(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    FAILED = "failed"


class ActorOrigin(StrEnum):
    """Foundation's two origins (``dw_jobs.started_by_origin``)."""

    OPERATOR = "operator"
    AGENT = "agent"


#: The guided New-dataset flow's steps, in order (FR-002.48). Served by ``/datasets/meta``.
GUIDED_STEPS: tuple[str, ...] = (
    "import",
    "goal",
    "profile",
    "curate",
    "label",
    "assemble",
    "export",
)


def check_in(column: str, enum: type[StrEnum]) -> str:
    """SQL for ``<column> IN (...)`` over an enum's values: the one way CHECKs are written."""
    return f"{column} IN (" + ", ".join(f"'{member.value}'" for member in enum) + ")"


def values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


# --- feature 006: calibration and review (FTDD 006 section 4.2) ------------------------------
# CHECK-constrained strings like the rest of this module (FTDD 006 names native enum types; the
# Foundation convention above wins and the discrepancy is recorded in the controls review).


class CalibrationSource(StrEnum):
    IMPORTED = "imported"
    REVIEW = "review"


class ScoreKind(StrEnum):
    PROBABILITY = "probability"
    DISTRIBUTION = "distribution"
    DISCRETE = "discrete"


class CheckResultValue(StrEnum):
    PASS = "pass"  # noqa: S105 - a check outcome, not a password
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class GateVerdict(StrEnum):
    """Exactly 008's ``CalibrationRef.verdict`` literals (FR-006.38)."""

    PASSES = "passes"
    FAILS = "fails"
    INVALID = "invalid"
    INSUFFICIENT = "insufficient"


class GateRule(StrEnum):
    """Exactly 008's ``CalibrationRef.rule`` literals (FR-006.38)."""

    OPERATOR_TARGET = "operator_target"
    HELD_OUT_RATER = "held_out_rater"
    DEFAULT_C3 = "default_c3"


class ReviewQueueKind(StrEnum):
    LABEL_REVIEW = "label_review"
    CALIBRATION_LABELING = "calibration_labeling"
    AUDIT = "audit"
    EXTERNAL = "external"


class ReviewQueueState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewDecisionKind(StrEnum):
    ACCEPT = "accept"
    OVERRIDE = "override"
    FLAG = "flag"
    REJECT = "reject"


class AuditState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    SUPERSEDED = "superseded"

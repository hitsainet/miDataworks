"""Feature 004's native operators: ``CURATION_OPERATORS`` is THE list (FTDD 004 §6.1, §6.2).

Imported once, by 003's ``operators/native/registrations.py``; the reachability test builds the
live registry and finds every entry, and deleting that import turns it red (M14).

``REPORT_OPERATORS`` maps a report kind to the operator whose ``name@version`` and manifest hash
identify a stored report (``services/curation/report_service.identity_of``), so a report computed
by the API, the worker or a recipe step carries one identity.
"""

from __future__ import annotations

from .cell_balancer import CellBalancer
from .chat_json_parser import ChatJsonParser
from .cluster import Cluster, ClusterBalancer
from .contamination import ContaminationCheck, Decontaminate
from .dedup_exact import DedupExact
from .dedup_minhash import DedupMinhash
from .filters import EmptyContent, LengthBand, NgramRepetition, TurnCountBand
from .leakage_check import LeakageCheck
from .metadata_value_filter import MetadataValueFilter
from .normaliser import Normaliser
from .profile import Profile
from .shortcut_audit import ShortcutAudit
from .split import Split
from .trl_validate import TrlValidate

CURATION_OPERATORS: tuple[type, ...] = (
    ShortcutAudit,
    CellBalancer,
    MetadataValueFilter,
    Split,
    Normaliser,
    ChatJsonParser,
    DedupExact,
    LengthBand,
    TurnCountBand,
    EmptyContent,
    NgramRepetition,
    TrlValidate,
    DedupMinhash,
    LeakageCheck,
    Profile,
    ContaminationCheck,
    Decontaminate,
    Cluster,
    ClusterBalancer,
)

REPORT_OPERATORS: dict[str, type] = {
    "shortcut_audit": ShortcutAudit,
    "leakage": LeakageCheck,
    "profile": Profile,
    "contamination": ContaminationCheck,
    "clusters": Cluster,
}

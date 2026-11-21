"""Feature 017's native behaviour operators (FTID 017 §3.1).

``BEHAVIOUR_OPERATORS`` is THE list, imported once by ``operators/native/registrations.py`` (its
one line there is the wiring its removal control deletes). Slice 2 shipped ``carrier_plan@1``;
slice 3 adds ``insert_element@1`` and ``derive_labels@1``; slice 4 ``screen_filter@1``; slice 5
``slot_siblings@1`` (and ``@1.1.0``, whose siblings' element column describes their span); ENH-006 D116
G6 ``calibration_labels@1.0.0``. None names a behaviour: vocabularies, shares and definitions are step
parameters.
"""

from __future__ import annotations

from .calibration_labels import CalibrationLabels
from .carrier_plan import CarrierPlan, CarrierPlanV11
from .check_carrier_ids import CheckCarrierIds
from .derive_labels import DeriveLabels
from .insert_element import InsertElement
from .screen_filter import ScreenFilter
from .slot_siblings import SlotSiblings, SlotSiblingsV11

BEHAVIOUR_OPERATORS: tuple[type, ...] = (
    CarrierPlan,
    CarrierPlanV11,
    CheckCarrierIds,
    InsertElement,
    DeriveLabels,
    ScreenFilter,
    SlotSiblings,
    SlotSiblingsV11,
    CalibrationLabels,
)

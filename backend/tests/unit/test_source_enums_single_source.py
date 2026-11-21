"""001 FTASKS 9.7: 001's ``Redistribution`` vocabulary and 008's licence classes are one set.

An annotation (001 FR-001.28) asserts a ``Redistribution`` value; 008's classifier maps it through
``ANNOTATION_CLASS`` onto a ``LicenceClass`` (FR-008.59). A value on one side the other does not
know would either be refused at classification (KeyError on a stored annotation) or never be
reachable. The spellings differ by design (``permits`` vs ``permits_redistribution``, recorded in
001 FTASKS 9.7), so the tie is by member NAME, both ways, and the mapping must be a bijection.
"""

from __future__ import annotations

from src.models.source_enums import Redistribution
from src.services.publishing.licence_table import ANNOTATION_CLASS, LicenceClass


def test_every_redistribution_value_has_exactly_one_licence_class() -> None:
    assert set(ANNOTATION_CLASS) == {r.value for r in Redistribution}


def test_every_licence_class_is_reachable_from_an_annotation() -> None:
    assert set(ANNOTATION_CLASS.values()) == set(LicenceClass)
    assert len(set(ANNOTATION_CLASS.values())) == len(ANNOTATION_CLASS)


def test_the_mapping_pairs_members_of_the_same_name() -> None:
    for member in Redistribution:
        assert ANNOTATION_CLASS[member.value] is LicenceClass[member.name], member

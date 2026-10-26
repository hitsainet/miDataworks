"""Config and split choice (001 FTASKS 6.1, 6.2; T-06; mutation control M14).

One config is chosen; several and none requested → ``config_required`` listing them; named but
absent → ``config_not_found``; none known → the library default; a split not in the config →
``split_not_found`` listing the splits. Never silently the first config.
"""

from __future__ import annotations

import pytest

from src.core.errors import AppError
from src.services.sources.config_choice import check_split, choose_config


class TestConfigChoice:
    def test_one_config_is_chosen(self) -> None:
        assert choose_config(["default"], None) == "default"

    def test_several_and_none_requested_is_refused_listing_them(self) -> None:
        with pytest.raises(AppError) as info:
            choose_config(["subtask-1", "subtask-2"], None)
        assert info.value.code == "config_required"
        assert info.value.details["configs"] == ["subtask-1", "subtask-2"]

    def test_named_but_absent(self) -> None:
        with pytest.raises(AppError) as info:
            choose_config(["a", "c"], "b")
        assert info.value.code == "config_not_found" and info.value.details["configs"] == ["a", "c"]

    def test_named_and_present_is_used_even_when_not_first(self) -> None:
        assert choose_config(["subtask-1", "subtask-2"], "subtask-2") == "subtask-2"

    def test_the_first_config_is_never_picked_silently(self) -> None:
        with pytest.raises(AppError):
            choose_config(["default", "other"], None)

    def test_duplicates_from_the_viewer_count_once(self) -> None:
        assert choose_config(["default", "default"], None) == "default"

    def test_none_known_uses_the_library_default(self) -> None:
        assert choose_config([], None) is None

    def test_split_not_in_config_lists_the_splits(self) -> None:
        with pytest.raises(AppError) as info:
            check_split(["train", "test"], "validation")
        assert info.value.code == "split_not_found" and info.value.details["splits"] == [
            "train",
            "test",
        ]
        assert check_split(["train"], None) is None

    def test_a_split_in_the_config_is_kept(self) -> None:
        assert check_split(["train", "test"], "test") == "test"

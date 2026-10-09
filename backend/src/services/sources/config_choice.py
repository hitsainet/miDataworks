"""Config and split choice (FR-001.1, FR-001.15; T-06; 001 FTID section 7). Never silently the
first config.

- requested and present -> it; requested and absent -> ``config_not_found`` listing configs;
- not requested and one config -> that one; several -> ``config_required`` listing them;
- not requested and none known -> ``None`` (the library default), recorded.

A requested split must be in the chosen config's splits, else ``split_not_found`` listing them.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...core.errors import AppError


def choose_config(configs: Sequence[str], requested: str | None) -> str | None:
    known = list(dict.fromkeys(configs))
    if requested:
        if known and requested not in known:
            raise AppError(
                f"This dataset has no config {requested!r}. Choose one of {known}.",
                code="config_not_found",
                status_code=409,
                details={"configs": known},
            )
        return requested
    if len(known) == 1:
        return known[0]
    if len(known) > 1:
        raise AppError(
            f"This dataset has {len(known)} configs; choose one: {known}.",
            code="config_required",
            status_code=409,
            details={"configs": known},
        )
    return None


def check_split(splits: Sequence[str], requested: str | None) -> str | None:
    if requested and splits and requested not in splits:
        raise AppError(
            f"The chosen config has no split {requested!r}. Choose one of {list(splits)}, or leave "
            "it empty for every split.",
            code="split_not_found",
            status_code=409,
            details={"splits": list(splits)},
        )
    return requested or None

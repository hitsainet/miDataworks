"""``synth_identifier@1`` and ``checksum_validate@1`` (e2e run gap G0, 2026-10-21).

The 2026-10-21 end-to-end run had no operator that makes a valid identifier with a check digit and controlled
perturbations, nor one that checks a value against a format and checksum rule. Both were
hand-built in Data Designer Jinja: 90-115 ``dd_expression`` steps per dictionary, two chained
recipes, a local linter and a simulator. These two operators replace that, and share ONE
implementation of every rule (``schemes.py``): the generator's ``valid`` flag is the validator's
verdict on the value it produced.

Both are deterministic labelers (they add columns, never change or drop a row): a labeler's other
outputs are the label's provenance, which feature 004's audit sets aside (``label_derived``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...manifest import ColumnSpec
from ...protocol import OperatorResult
from . import schemes
from .common import (
    PREFIX_PATTERN,
    append_outputs,
    manifest,
    output_names,
    pairs,
    refuse,
    text_values,
)

_SCHEME = {
    "type": "string",
    "enum": list(schemes.SCHEME_NAMES),
    "title": "Scheme",
    "x-hint": "card_pan (Luhn + issuer range), ssn_us (SSA ranges), aba_routing (3-7-1), iban "
    "(MOD 97-10 + country length and BBAN), nhs (Mod 11), aadhaar (Verhoeff).",
}
_COUNTRIES = {
    "type": "array",
    "items": {"type": "string", "enum": sorted(schemes.IBAN_REGISTRY)},
    "uniqueItems": True,
    "title": "IBAN countries",
    "x-hint": "iban only: the listed countries (default: all 45 in the registry).",
}
_CHECK_BBAN = {
    "type": "boolean",
    "default": True,
    "title": "Check the BBAN format",
    "x-hint": "iban only: the country's BBAN letter/digit layout must match too.",
}


def _scheme_opts(params: Mapping[str, Any]) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if params.get("countries"):
        opts["countries"] = list(params["countries"])
    if params.get("check_bban") is not None:
        opts["check_bban"] = bool(params["check_bban"])
    if params.get("brands"):
        opts["brands"] = list(params["brands"])
    if params.get("prefix_set"):
        opts["prefix_set"] = str(params["prefix_set"])
    return opts


def _only_for(params: Mapping[str, Any], scheme: str) -> None:
    """Refuse a scheme-specific parameter on the wrong scheme (it would be ignored silently)."""
    owners = {
        "countries": "iban",
        "check_bban": "iban",
        "brands": "card_pan",
        "prefix_set": "card_pan",
    }
    for name, owner in owners.items():
        if params.get(name) is not None and scheme != owner:
            raise refuse(
                "params_invalid",
                f"{name} applies to the {owner} scheme only; this step uses {scheme}.",
                {"param": name, "scheme": scheme},
            )


# --------------------------------------------------------------------------------------------
# synth_identifier
# --------------------------------------------------------------------------------------------

SYNTH_OUTPUTS = ("value", "scheme", "perturbation", "valid", "detail")

SYNTH_MANIFEST = manifest(
    "synth_identifier",
    "labeler",
    "Makes one synthetic identifier per row - payment card, US SSN, ABA routing, IBAN, NHS or "
    "Aadhaar - from a seed and the row's identity (no model, no source data): a share of valid "
    "values in the delimiter styles you choose, the rest with a named perturbation "
    "(bad_check_digit, wrong_length, mixed_delimiters, adjacent_letters, bad_prefix / bad_range, "
    "masked). Writes value, scheme, perturbation and valid (checksum_validate's verdict on the "
    "value), plus detail (card brand, IBAN country, SSN range). Start from "
    "dataworks_create_index_source for rows with no upload.",
    {
        "scheme": _SCHEME,
        "seed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2147483647,
            "default": 0,
            "title": "Seed",
            "x-hint": "Same seed and rows, same values.",
        },
        "valid_share": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "default": 0.5,
            "title": "Share of valid values",
            "x-unit": "fraction of rows",
        },
        "perturbations": {
            "type": "array",
            "items": {"type": "string", "enum": list(schemes.PERTURBATIONS)},
            "title": "Perturbations",
            "x-hint": "Drawn uniformly for the invalid share; repeat a name to weight it. Default: "
            "every perturbation the scheme supports.",
        },
        "delimiters": {
            "type": "array",
            "items": {"type": "string", "enum": list(schemes.STYLES)},
            "uniqueItems": True,
            "title": "Delimiter styles",
            "x-hint": "Layouts of valid values: none, space, dash, dot, slash, colon (each scheme "
            "allows its own). Default: none, space and dash where allowed.",
        },
        "brands": {
            "type": "array",
            "items": {"type": "string", "enum": list(schemes.CARD_BRANDS)},
            "uniqueItems": True,
            "title": "Card brands",
            "x-hint": "card_pan only (default: all six).",
        },
        "prefix_set": {
            "type": "string",
            "enum": ["issuer", "test"],
            "title": "Card prefixes",
            "x-hint": "card_pan only: issuer ranges (default) or only the BINs of the networks' "
            "published test card numbers.",
        },
        "countries": _COUNTRIES,
        "check_bban": _CHECK_BBAN,
        "output_prefix": {
            "type": "string",
            "pattern": PREFIX_PATTERN,
            "default": "",
            "title": "Output prefix",
            "x-hint": "Prepended to value, scheme, perturbation, valid and detail.",
        },
    },
    ["scheme"],
    output_columns=(
        ColumnSpec(name="value", type="string", role="metadata"),
        ColumnSpec(name="scheme", type="string", role="metadata"),
        ColumnSpec(name="perturbation", type="string", role="metadata"),
        ColumnSpec(name="valid", type="bool", role="metadata"),
        ColumnSpec(name="detail", type="string", role="metadata", required=False),
    ),
)


def _synth_plan(params: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    scheme_name = str(params["scheme"])
    scheme = schemes.SCHEMES[scheme_name]
    _only_for(params, scheme_name)
    wanted = list(params.get("perturbations") or scheme.perturbations)
    unsupported = sorted({p for p in wanted if p not in scheme.perturbations})
    if unsupported:
        raise refuse(
            "params_invalid",
            f"{scheme_name} cannot make {unsupported}. It supports: "
            + ", ".join(scheme.perturbations)
            + ".",
            {"param": "perturbations", "unsupported": unsupported},
        )
    styles = list(
        params.get("delimiters") or [s for s in ("none", "space", "dash") if s in scheme.styles]
    )
    bad_styles = sorted({s for s in styles if s not in scheme.styles})
    if bad_styles:
        raise refuse(
            "params_invalid",
            f"{scheme_name} values cannot be laid out with {bad_styles}: its delimiters are "
            f"{scheme.delimiters!r}. Allowed styles: " + ", ".join(scheme.styles) + ".",
            {"param": "delimiters", "unsupported": bad_styles},
        )
    return scheme_name, wanted, styles


class SynthIdentifier:
    manifest = SYNTH_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return [], output_names(params, "", SYNTH_OUTPUTS), []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        return self._run(batch, params, wrong_length_in_style=False)

    def _run(
        self, batch: pa.Table, params: Mapping[str, Any], *, wrong_length_in_style: bool
    ) -> OperatorResult:
        scheme_name, perturbations, styles = _synth_plan(params)
        opts = _scheme_opts(params)
        seed = int(params.get("seed") or 0)
        share = float(
            params.get("valid_share", 0.5) if params.get("valid_share") is not None else 0.5
        )
        values, kinds, flags, details = [], [], [], []
        for key, occurrence in pairs(batch):
            rng = schemes.row_rng(seed, scheme_name, key, occurrence)
            perturbation = "none" if rng.random() < share else rng.choice(perturbations)
            style = rng.choice(styles)
            try:
                made = schemes.generate(
                    scheme_name,
                    rng,
                    perturbation=perturbation,
                    style=style,
                    wrong_length_in_style=wrong_length_in_style,
                    **opts,
                )
            except ValueError as exc:
                raise refuse("params_invalid", str(exc), {"scheme": scheme_name}) from None
            values.append(made.value)
            kinds.append(made.perturbation)
            flags.append(made.valid)
            details.append(made.detail)
        names = output_names(params, "", SYNTH_OUTPUTS)
        output = append_outputs(
            batch,
            [
                (names[0], pa.array(values, pa.string())),
                (names[1], pa.array([scheme_name] * batch.num_rows, pa.string())),
                (names[2], pa.array(kinds, pa.string())),
                (names[3], pa.array(flags, pa.bool_())),
                (names[4], pa.array(details, pa.string())),
            ],
            self.manifest.ref_text,
        )
        return OperatorResult(output=output, output_roles=dict.fromkeys(names, "metadata"))


SYNTH_V11_VERSION = "1.1.0"
#: ``synth_identifier@1.1.0`` (017 pilot, ENH-006 D67): @1's parameters plus the wrong-length
#: layout. @1 is unchanged (its manifest hash and every recorded output are pinned by
#: ``tests/unit/rules/test_existing_operator_goldens.py``).
SYNTH_V11_PARAMS: dict[str, Any] = {
    **SYNTH_MANIFEST.params_schema["properties"],
    "wrong_length_layout": {
        "type": "string",
        "enum": ["none", "style"],
        "default": "none",
        "title": "Wrong-length layout",
        "x-hint": "none (default, as @1): a wrong_length value has no delimiter. style: it is "
        "laid out in the row's delimiter style, the last group taking the difference "
        "(123-45-67890), so a planned form with delimiters gets them.",
    },
}

SYNTH_V11_MANIFEST = manifest(
    "synth_identifier",
    "labeler",
    SYNTH_MANIFEST.description
    + " 1.1.0: wrong_length_layout=style lays a wrong_length value out in the delimiter style.",
    SYNTH_V11_PARAMS,
    ["scheme"],
    output_columns=SYNTH_MANIFEST.output_columns,
    version=SYNTH_V11_VERSION,
)


class SynthIdentifierV11(SynthIdentifier):
    """``synth_identifier@1.1.0``: @1 plus ``wrong_length_layout``. Absent or ``none``, every row
    is byte-identical to @1's (no random draw changes; only a wrong_length value's layout can)."""

    manifest = SYNTH_V11_MANIFEST

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        in_style = (params.get("wrong_length_layout") or "none") == "style"
        return self._run(batch, params, wrong_length_in_style=in_style)


# --------------------------------------------------------------------------------------------
# checksum_validate
# --------------------------------------------------------------------------------------------

CHECK_OUTPUTS = ("valid", "reason", "tier", "layout", "normalised", "detail", "start", "end")

CHECK_MANIFEST = manifest(
    "checksum_validate",
    "labeler",
    "Checks a value against an identifier scheme's rules - the same rules synth_identifier uses: "
    "length, prefix or range, check digit (Luhn, ABA 3-7-1, MOD 97-10, Mod 11, Verhoeff) - and "
    "the documented confidence tier: none, low (the number checks out but its layout or a "
    "neighbouring letter keeps it from medium) or medium. Deterministic, no model. Writes "
    "<prefix>valid, reason (ok, or the first rule that failed: wrong_length, bad_check_digit, "
    "bad_prefix, bad_range, illegal_character, alphanumeric_boundary, irregular_layout, ...), "
    "tier, layout, normalised, detail, start and end (the value's character span in "
    "before + value + after, for phrase_proximity). mode=value checks the column as the value "
    "(before_column/after_column give the neighbouring text whose last/first character is the "
    "boundary); mode=scan finds the best candidate in free text.",
    {
        "column": {"type": "string", "minLength": 1, "title": "Column"},
        "scheme": _SCHEME,
        "mode": {
            "type": "string",
            "enum": ["value", "scan"],
            "default": "value",
            "title": "Mode",
            "x-hint": "value: the column holds the candidate. scan: search free text for "
            "candidates and keep the best (highest tier, first on a tie).",
        },
        "before_column": {
            "type": "string",
            "minLength": 1,
            "title": "Text before the value",
            "x-hint": "mode=value: its last character is the left boundary; its length is the "
            "value's start offset.",
        },
        "after_column": {
            "type": "string",
            "minLength": 1,
            "title": "Text after the value",
            "x-hint": "mode=value: its first character is the right boundary.",
        },
        "countries": _COUNTRIES,
        "check_bban": _CHECK_BBAN,
        "output_prefix": {
            "type": "string",
            "pattern": PREFIX_PATTERN,
            "default": "cv_",
            "title": "Output prefix",
        },
    },
    ["column", "scheme"],
    output_columns=tuple(
        ColumnSpec(
            name="cv_" + n,
            type={"valid": "bool", "start": "int64", "end": "int64"}.get(n, "string"),
            role="metadata",
            required=n not in ("detail", "start", "end"),
        )
        for n in CHECK_OUTPUTS
    ),
)


def check_one(
    scheme: schemes.Scheme,
    value: str | None,
    before: str,
    after: str,
    mode: str,
    opts: dict[str, Any],
) -> tuple[schemes.Verdict, int | None, int | None]:
    """The verdict and the value's span (character offsets in ``before + value + after``)."""
    if value is None:
        return schemes.Verdict(False, "null", "none", "none", "", None), None, None
    if mode == "value":
        verdict = scheme.check(value, before, after, **opts)
        return verdict, len(before), len(before) + len(value)
    # 016 FR-016.3: the best candidate is computed from the validator library's all-candidates
    # scan, ranked as before: max() returns the FIRST maximum, as the old strict ">" loop did.
    from ....rules.validators import resolve

    found = resolve(scheme.name, 1).scan(value, opts, before=before, after=after)  # type: ignore[call-arg]
    best = max(
        found, key=lambda c: (schemes.TIER_RANK[c.verdict.tier], c.verdict.valid), default=None
    )
    if best is None:
        return schemes.Verdict(False, "no_candidate", "none", "none", "", None), None, None
    return best.verdict, len(before) + best.start, len(before) + best.end


class ChecksumValidate:
    manifest = CHECK_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads = [
            str(params[k]) for k in ("column", "before_column", "after_column") if params.get(k)
        ]
        return reads, output_names(params, "cv_", CHECK_OUTPUTS), []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        scheme_name = str(params["scheme"])
        _only_for(params, scheme_name)
        scheme = schemes.SCHEMES[scheme_name]
        mode = str(params.get("mode") or "value")
        if mode == "scan" and (params.get("before_column") or params.get("after_column")):
            raise refuse(
                "params_invalid",
                "before_column and after_column apply to mode=value; mode=scan reads the "
                "boundaries from the text itself.",
            )
        opts = _scheme_opts(params)
        values = text_values(batch, str(params["column"]))
        n = batch.num_rows
        befores = (
            text_values(batch, str(params["before_column"]), "before_column")
            if params.get("before_column")
            else [""] * n
        )
        afters = (
            text_values(batch, str(params["after_column"]), "after_column")
            if params.get("after_column")
            else [""] * n
        )
        cols: dict[str, list[Any]] = {k: [] for k in CHECK_OUTPUTS}
        for value, before, after in zip(values, befores, afters, strict=True):
            verdict, start, end = check_one(scheme, value, before or "", after or "", mode, opts)
            cols["valid"].append(verdict.valid)
            cols["reason"].append(verdict.reason)
            cols["tier"].append(verdict.tier)
            cols["layout"].append(verdict.layout)
            cols["normalised"].append(verdict.normalised)
            cols["detail"].append(verdict.detail)
            cols["start"].append(start)
            cols["end"].append(end)
        names = output_names(params, "cv_", CHECK_OUTPUTS)
        types = {"valid": pa.bool_(), "start": pa.int64(), "end": pa.int64()}
        output = append_outputs(
            batch,
            [
                (name, pa.array(cols[key], types.get(key, pa.string())))
                for name, key in zip(names, CHECK_OUTPUTS, strict=True)
            ],
            self.manifest.ref_text,
        )
        return OperatorResult(output=output, output_roles=dict.fromkeys(names, "metadata"))


OPERATORS: tuple[type, ...] = (SynthIdentifier, SynthIdentifierV11, ChecksumValidate)

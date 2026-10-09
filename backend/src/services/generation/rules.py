"""Pure guards, identities, seeds and request bodies for generation (FTID 007 section 7.1).

PURE: the standard library, numpy and :mod:`.steering` only (``test_generation_pure_imports.py``).
Every guard here is called from at least two named places — the API (plan/start) and the worker —
and ``test_guard_call_sites.py`` walks the AST to keep it so (assert the CALL, not the text).

The rules:

- **Held-out first** (FR-007.35): an input version with no held-out split is refused
  ``HELD_OUT_MISSING``, naming 004's split operator as the next step (P-23).
- **Seed splits** (FR-007.36): a seed split that is held out is refused ``HELD_OUT_SEED``.
- **One axis** (P-22): two settings must differ on exactly ONE SAE feature index, on the same
  model, SAE and layer. A cluster profile at two intensities moves every member, so it is refused
  with every member listed — exactly P-22's intent.
- **Judge independence** (T-35): identity = (model ID, revision or ``"not reported"``, set hash or
  ``"none"``). The endpoint is IGNORED: the same model on two base URLs conflicts, and two
  ``"not reported"`` revisions of one model ID conflict (sameness cannot be ruled out).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from .. import identity
from . import steering

NOT_REPORTED = "not reported"
NO_STEERING = "none"


class GenerationRuleError(Exception):
    """A refusal with a stable code, an HTTP status and details (mapped to the envelope)."""

    status = 422

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class HeldOutMissing(GenerationRuleError):
    status = 409


class HeldOutSeed(GenerationRuleError):
    status = 422


class NotOneAxis(GenerationRuleError):
    status = 422


class JudgeIsGenerator(GenerationRuleError):
    status = 422


# --- settings and effective sets ------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveSet:
    """What one setting asks miLLM to apply: effective strengths (stored value × λ, unclamped)."""

    kind: Literal["none", "profile", "inline"]
    model_id: str | None = None
    sae_id: str | None = None
    layer: int | None = None
    #: index -> effective strength (zeros kept out).
    features: Mapping[int, float] = field(default_factory=dict)
    profile_id: str | None = None
    profile_name: str | None = None
    profile_updated_at: str | None = None
    intensity: float | None = None

    def sorted_pairs(self) -> list[tuple[int, float]]:
        return sorted((int(i), float(s)) for i, s in self.features.items())

    def applied(self) -> list[tuple[int, float]]:
        """The pairs miLLM applies (clamped, zeros removed), sorted."""
        return sorted(steering.applied_pairs(self.sorted_pairs()).items())

    def set_hash(self) -> str | None:
        """The applied set's hash, or None for ``none`` (or an inline set without an SAE)."""
        if self.kind == "none" or self.sae_id is None:
            return None
        return steering.steering_set_hash(self.sae_id, self.applied())

    def expected(self) -> steering.Expected:
        return steering.Expected(
            self.kind,
            profile_name=self.profile_name,
            intensity=self.intensity,
            sae_id=self.sae_id,
            layer=self.layer,
            features=tuple(self.applied()),
            set_hash=self.set_hash(),
        )


def resolve_effective_set(
    setting: Mapping[str, Any], profile: Mapping[str, Any] | None
) -> EffectiveSet:
    """A request setting (``{kind: none|profile|inline, ...}``) plus, for a profile, miLLM's
    ``ProfileResponse`` (``steering`` stored at λ=1, ``intensity`` the dial; miLLM
    ``schemas/profile.py``) → its effective set. miLLM applies ``clamp(value × λ)``
    (``steering_report.py``), so the effective value is ``value × λ`` before the clamp."""
    kind = setting.get("kind")
    if kind == "none":
        return EffectiveSet("none")
    if kind == "profile":
        if profile is None:
            raise GenerationRuleError("PROFILE_NOT_FOUND", "The profile was not read.")
        lam = float(profile["intensity"]) if profile.get("intensity") is not None else 1.0
        raw = profile.get("steering") or {}
        features = {}
        for index, value in raw.items():
            effective = float(value) * lam
            if effective != 0.0:
                features[int(index)] = effective
        layer = profile.get("layer")
        return EffectiveSet(
            "profile",
            model_id=profile.get("model_id"),
            sae_id=profile.get("sae_id"),
            layer=int(layer) if layer is not None else None,
            features=features,
            profile_id=str(profile["id"]),
            profile_name=str(profile["name"]),
            profile_updated_at=str(profile["updated_at"]),
            intensity=lam,
        )
    if kind == "inline":
        features = {}
        for item in setting.get("features") or []:
            strength = float(item["strength"])
            if strength != 0.0:
                features[int(item["index"])] = strength
        return EffectiveSet(
            "inline",
            model_id=setting.get("model_id"),
            sae_id=setting.get("sae_id"),
            layer=setting.get("layer"),
            features=features,
        )
    raise GenerationRuleError("SETTING_INVALID", f"Unknown steering setting kind {kind!r}.")


class NotComparable(NotOneAxis):
    """The two settings sit on different models, SAEs or layers."""


def differing_indices(a: EffectiveSet, b: EffectiveSet) -> list[int]:
    """Indices whose effective strength differs (an absent index counts as 0.0; EXACT compare).

    Raises :class:`NotComparable` when model, SAE or layer differ (each known on both sides).
    """
    different = [
        name
        for name, x, y in (
            ("model", a.model_id, b.model_id),
            ("sae", a.sae_id, b.sae_id),
            ("layer", a.layer, b.layer),
        )
        if x is not None and y is not None and x != y
    ]
    if different:
        raise NotComparable(
            "NOT_ONE_AXIS",
            f"The two settings sit on different {', '.join(different)}; a steered pair must "
            "differ on one feature of one SAE.",
            {"differing": [], "not_comparable": different},
        )
    indices = sorted(set(a.features) | set(b.features))
    return [i for i in indices if float(a.features.get(i, 0.0)) != float(b.features.get(i, 0.0))]


def one_axis_or_raise(a: EffectiveSet, b: EffectiveSet) -> int:
    """The ONE differing index, or ``NOT_ONE_AXIS`` listing every differing index (P-22)."""
    diff = differing_indices(a, b)
    if len(diff) != 1:
        if not diff:
            message = "The two settings are identical: a steered pair needs one feature to differ."
        else:
            message = (
                f"The two settings differ on {len(diff)} features ({', '.join(map(str, diff[:12]))}"
                f"{'…' if len(diff) > 12 else ''}); a steered pair must differ on exactly one."
            )
        raise NotOneAxis(
            "NOT_ONE_AXIS",
            message,
            {
                "differing": [
                    {"index": i, "a": a.features.get(i, 0.0), "b": b.features.get(i, 0.0)}
                    for i in diff
                ]
            },
        )
    return diff[0]


# --- identities and judge independence -------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    model_id: str
    revision: str
    set_hash: str

    def as_dict(self) -> dict[str, str]:
        return {"model_id": self.model_id, "revision": self.revision, "set_hash": self.set_hash}


def generator_identity(model_id: str, revision: str | None, set_hash: str | None) -> Identity:
    """(model, revision or "not reported", set hash or "none"). The endpoint is not part of it."""
    return Identity(str(model_id), revision or NOT_REPORTED, set_hash or NO_STEERING)


def identity_from(value: Mapping[str, Any]) -> Identity:
    return generator_identity(
        str(value["model_id"]),
        None if value.get("revision") in (None, NOT_REPORTED) else str(value["revision"]),
        None if value.get("set_hash") in (None, NO_STEERING) else str(value["set_hash"]),
    )


def judge_conflicts(judge: Identity, generators: Sequence[Identity]) -> list[dict[str, Any]]:
    """Every generator identity the judge cannot be independent of (T-35).

    Same model ID and same steering hash conflict unless BOTH revisions are reported and differ.
    A "not reported" revision on either side cannot show the models differ, so it conflicts.
    """
    out: list[dict[str, Any]] = []
    for generator in generators:
        if generator.model_id != judge.model_id or generator.set_hash != judge.set_hash:
            continue
        both_reported = NOT_REPORTED not in (generator.revision, judge.revision)
        if both_reported and generator.revision != judge.revision:
            continue
        out.append({"judge": judge.as_dict(), "generator": generator.as_dict()})
    return out


def conflict_error(
    judge: Identity, conflicts: Sequence[dict[str, Any]], *, inherited_from: str | None
) -> JudgeIsGenerator:
    """``JUDGE_IS_GENERATOR`` naming both identities, and the inheritance when present
    (FR-007.26). Built from :func:`judge_conflicts`' answer; never decides anything itself."""
    inherit = (
        " The generation endpoint inherits the judge's endpoint and model; set a separate "
        "generation model in Settings → Endpoints → generation, or choose another judge."
        if inherited_from == "judge"
        else " Choose a judge model that differs from the generator, or steer the generator."
    )
    return JudgeIsGenerator(
        "JUDGE_IS_GENERATOR",
        f"The judge ({judge.model_id}, revision {judge.revision}, steering {judge.set_hash}) is "
        f"the same model as the generator; a model may not grade its own output.{inherit}",
        {"conflicts": list(conflicts), "inherited_from": inherited_from},
    )


# --- held-out guards ------------------------------------------------------------------------

#: 004's split operator, named as the next step (P-23).
SPLIT_OPERATOR = "split"


def held_out_guard(
    splits: Sequence[Mapping[str, Any]], held_out_origin_version_id: str | None
) -> list[str]:
    """The input version's held-out split names, or ``HELD_OUT_MISSING`` (FR-007.35)."""
    names = [str(s["name"]) for s in splits if bool(s.get("held_out"))]
    if not names or held_out_origin_version_id is None:
        raise HeldOutMissing(
            "HELD_OUT_MISSING",
            "This version has no held-out split. Split it first, so generated rows can never be "
            "drawn from or written into the test rows.",
            {
                "next_step": {
                    "operator": SPLIT_OPERATOR,
                    "feature": "004",
                    "action": "Add a split step (004 'split') with a held-out split, build, "
                    "then generate from the new version.",
                }
            },
        )
    return names


def seed_split_guard(seed_splits: Sequence[str], held_out: Sequence[str]) -> None:
    """``HELD_OUT_SEED`` when a seed split is held out (FR-007.36)."""
    bad = sorted(set(seed_splits) & set(held_out))
    if bad:
        raise HeldOutSeed(
            "HELD_OUT_SEED",
            f"Seed split(s) {bad} are held out. Seed rows come only from training splits.",
            {"held_out_seed_splits": bad, "held_out": list(held_out)},
        )


def row_is_eligible_seed(origin: Any, split: Any, seed_splits: Sequence[str]) -> bool:
    """The worker's per-row re-check: a source row from a seed split (FR-007.36)."""
    return origin == "source" and split in set(seed_splits)


# --- seeds, indices and bodies --------------------------------------------------------------


def select_seeds(keys: Sequence[str], n: int, seed: int) -> list[str]:
    """``n`` distinct keys chosen with ``numpy.random.default_rng(seed)`` from the key list
    SORTED first (so the choice depends on the set, not the read order); returned in the order
    drawn, which is the recorded order of the run."""
    ordered = sorted(set(keys))
    if not ordered or n <= 0:
        return []
    take = min(int(n), len(ordered))
    rng = np.random.default_rng(int(seed))
    picks = rng.choice(len(ordered), size=take, replace=False)
    return [ordered[int(i)] for i in picks]


def response_seed(run_seed: int, prompt_key: str, response_index: int) -> int:
    """The per-response seed, from ``identity.response_seed`` (the one hashing module)."""
    return identity.response_seed(run_seed, prompt_key, response_index)


def record_index(
    seed_position: int, response_index: int, n_responses: int, side: str | None = None
) -> int:
    """Deterministic order: seed order × N + response index, then side (``a`` before ``b``)."""
    base = int(seed_position) * int(n_responses) + int(response_index)
    if side is None:
        return base
    return base * 2 + (0 if side == "a" else 1)


def body_overrides(
    kind: str,
    *,
    profile_name: str | None,
    sae_id: str | None,
    features: Iterable[tuple[int, float]],
    steering_supported: bool,
    is_millm: bool,
) -> dict[str, Any]:
    """The request-body fields one side adds (FTID 007 section 3.2; miLLM FR-28.1.1, 28.2.2).

    A non-miLLM endpoint gets nothing (it has no steering contract). Unsteered on miLLM sends the
    explicit empty set ``{"steering": {"features": []}}`` once inline steering is served, so a
    globally active profile cannot steer the response silently.
    """
    if not is_millm:
        return {}
    if kind == "profile":
        return {"profile": profile_name}
    if kind == "inline":
        body: dict[str, Any] = {
            "features": [{"index": int(i), "strength": float(s)} for i, s in features]
        }
        if sae_id:
            body["sae_id"] = sae_id
        return {"steering": body}
    return {"steering": {"features": []}} if steering_supported else {}


def render_template(prompt: str, values: Mapping[str, Any]) -> str:
    """Fill ``{column}`` placeholders; ``{{`` and ``}}`` stay literal braces."""
    out: list[str] = []
    i = 0
    while i < len(prompt):
        ch = prompt[i]
        if prompt.startswith("{{", i):
            out.append("{")
            i += 2
            continue
        if prompt.startswith("}}", i):
            out.append("}")
            i += 2
            continue
        if ch == "{":
            end = prompt.find("}", i)
            if end == -1:
                out.append(prompt[i:])
                break
            name = prompt[i + 1 : end]
            value = values.get(name)
            out.append("" if value is None else str(value))
            i = end + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def placeholders(prompt: str) -> list[str]:
    """The ``{column}`` names a template uses, in first-use order."""
    names: list[str] = []
    i = 0
    while i < len(prompt):
        if prompt.startswith("{{", i) or prompt.startswith("}}", i):
            i += 2
            continue
        if prompt[i] == "{":
            end = prompt.find("}", i)
            if end == -1:
                break
            name = prompt[i + 1 : end]
            if name and name not in names:
                names.append(name)
            i = end + 1
            continue
        i += 1
    return names

"""The reward bundle for TRL's GRPO trainer (008 FR-008.46 - FR-008.49; FTASKS 13.x; M4).

A bundle is exported FROM a version (the GRPO prompt set it serves, whose identity the manifest
records) WITH a 005 rubric and a judge model. It holds:

- ``reward.py`` — copied byte-for-byte from ``reward_bundle_template/reward.py``: standard library
  only, TRL's reward signature, deterministic settings and a recorded seed, retry with
  ``Retry-After`` then :class:`RewardJudgeError`, a parse-failure counter, never a silent zero;
- ``rubric.json`` — the rubric as ``midataworks.rubric/v1`` (name, version, body);
- ``config.json`` — judge model, rubric reference, verdict → reward mapping, sampling, seed,
  retry policy and the NAMES of the environment variables holding the endpoint and key;
- ``README.md`` — how to use it, and R-03.53's sentence: a reward score is not an evaluation;
- the handoff manifest (``RewardBundleContent``).

No URL, key or secret is written into any file (FR-008.47; ``test_reward_bundle.py``).
Only ``pointwise`` and ``binary`` rubrics are accepted (13.1): a pairwise or stepwise answer is not
one number per completion.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...core.canonical_json import canonical_json
from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.errors import AppError
from ...core.storage import atomic_write_bytes, export_dir
from ...models.publish import Export, ExportStatus
from ...models.rubric import Rubric
from ...models.version import Version
from ..code_rules import carry as code_rule_carry
from ..decision_template_service import export_rubric_document
from ..identity import bytes_sha256, file_sha256
from ..publishing.card import caveats_from_outcomes
from ..publishing.check_inputs import assemble, manifest_source
from ..publishing.checks import evaluate_checks
from ..publishing.held_out_caveat import with_held_out_caveat
from ..publishing.manifest_builder import MANIFEST_FILE, build_document, manifest_bytes
from ..publishing.session_data import with_session_caveat
from ..rules import carry

TEMPLATE = Path(__file__).resolve().parent / "reward_bundle_template" / "reward.py"
STYLES = ("pointwise", "binary")
#: The fields ``reward.py`` fills itself for every completion (``judge_reward``/``render``); every
#: other rubric field must be a column of the version, which TRL passes as a keyword list.
FILLED_BY_REWARD = ("completion", "prompt", "question")
BASE_URL_ENV = "MIDATAWORKS_JUDGE_BASE_URL"
API_KEY_ENV = "MIDATAWORKS_JUDGE_API_KEY"  # noqa: S105 - the NAME of a variable, not a key
NOT_AN_EVALUATION = (
    "A reward score is not an evaluation result. Evaluation needs a separate held-out detector "
    "or judge."
)


class RewardBundleRefusal(AppError):
    status_code = 422


def rubric_or_refuse(session: Session, rubric_id: str | None) -> Rubric:
    if not rubric_id:
        raise RewardBundleRefusal(
            "A reward bundle names the rubric its judge applies (rubric_id).",
            code="reward_rubric_required",
        )
    rubric = session.get(Rubric, rubric_id)
    if rubric is None:
        raise RewardBundleRefusal(f"No rubric {rubric_id}.", code="rubric_not_found")
    if rubric.style not in STYLES:
        raise RewardBundleRefusal(
            f"Rubric {rubric.name} v{rubric.version} is {rubric.style}; a reward bundle takes a "
            "pointwise or binary rubric, which gives one verdict per completion.",
            code="reward_rubric_style_unsupported",
            details={"style": rubric.style, "accepted": list(STYLES)},
        )
    return rubric


def verdict_scores(rubric: Rubric, given: dict[str, float] | None) -> dict[str, float]:
    """Every allowed verdict's reward: as given, or the verdict's own number when every verdict
    is numeric (a 1-5 pointwise scale). Anything else must be stated; nothing is guessed."""
    allowed = [str(v) for v in rubric.body["allowed_verdicts"]]
    if given is None:
        try:
            return {v: float(v) for v in allowed}
        except ValueError:
            raise RewardBundleRefusal(
                f"Rubric {rubric.name} has verdicts {allowed}; state each one's reward in "
                "verdict_scores.",
                code="reward_scale_required",
                details={"allowed_verdicts": allowed},
            ) from None
    missing = [v for v in allowed if v not in given]
    unknown = [v for v in given if v not in allowed]
    if missing or unknown:
        raise RewardBundleRefusal(
            "verdict_scores must give a reward for every allowed verdict and nothing else.",
            code="reward_scale_incomplete",
            details={"missing": missing, "unknown": unknown, "allowed_verdicts": allowed},
        )
    return {v: float(given[v]) for v in allowed}


def rubric_fields(rubric: Rubric) -> set[str]:
    """The fields the rubric's messages render. 005 refuses, when a rubric is created, any
    ``{name}`` that is not one of its ``input_fields`` (or ``question``), so the declaration is
    the complete list; ``test_the_check_relies_on_005_refusing_undeclared_placeholders`` pins
    that precondition here."""
    return {str(f) for f in rubric.body.get("input_fields") or []}


def fields_or_refuse(rubric: Rubric, version_columns: set[str]) -> None:
    """Refuse a rubric the bundle cannot render, or one whose reward ignores the completion.

    Found live on 2026-10-17: a labeling rubric reading ``{text}`` exported without complaint and its
    ``reward.py`` raised on the first completion. A rubric that never reads ``{completion}`` would
    give every completion of a prompt the same reward, so GRPO's advantages are zero and training
    silently learns nothing; that is refused too, never guessed around."""
    needed = rubric_fields(rubric)
    fillable = set(FILLED_BY_REWARD) | version_columns
    unfillable = sorted(needed - fillable)
    if unfillable:
        raise RewardBundleRefusal(
            f"Rubric {rubric.name} v{rubric.version} reads {', '.join(unfillable)}, which the "
            "reward function cannot fill: it fills completion, prompt and question, and every "
            "other field from a column of the version. Use a rubric written for completions.",
            code="reward_rubric_fields_unfillable",
            details={"unfillable": unfillable, "fillable": sorted(fillable)},
        )
    if "completion" not in needed:
        raise RewardBundleRefusal(
            f"Rubric {rubric.name} v{rubric.version} never reads {{completion}}, so every "
            "completion of a prompt would get the same reward and GRPO would learn nothing. "
            "Use a rubric whose messages render {completion}.",
            code="reward_rubric_ignores_completion",
            details={"fields": sorted(needed)},
        )


def plan(session: Session, params: dict[str, Any], version: Version) -> dict[str, Any]:
    """Validate before a job exists; returns the facts the export row stores."""
    rubric = rubric_or_refuse(session, params.get("rubric_id"))
    fields_or_refuse(
        rubric, {c for c in version.column_roles or {} if not str(c).startswith("_dw_")}
    )
    judge = (params.get("judge_model_id") or "").strip()
    if not judge:
        raise RewardBundleRefusal(
            "A reward bundle names its judge model (judge_model_id).", code="reward_judge_required"
        )
    scores = verdict_scores(rubric, params.get("verdict_scores"))
    return {
        "rubric_ref": {
            "id": rubric.id,
            "name": rubric.name,
            "version": str(rubric.version),
            "content_sha256": rubric.content_hash,
            "style": rubric.style,
        },
        "verdict_scores": scores,
        "judge_model_id": judge,
    }


def _readme(config: dict[str, Any]) -> str:
    rubric = config["rubric"]
    return f"""# Reward bundle: {rubric['name']} v{rubric['version']}

Written by miDataworks for TRL {config['trl_version']}'s GRPO trainer.

{NOT_AN_EVALUATION}

## Use

```python
from reward import judge_reward
trainer = GRPOTrainer(..., reward_funcs=[judge_reward])
```

Set the judge's OpenAI-compatible base URL (ending in `/v1`) in `{config['base_url_env']}` and, if
it needs one, its key in `{config['api_key_env']}`. Nothing in this bundle holds a URL or a key.

- Judge model: `{config['judge_model_id']}`; rubric `{rubric['name']}` v{rubric['version']}
  ({rubric['style']}, sha256 `{rubric['content_sha256']}`).
- Each verdict's reward: {', '.join(f'`{k}` = {v}' for k, v in config['verdict_scores'].items())}.
- Deterministic settings {json.dumps(config['sampling'])}; seed {config['seed']} plus the
  completion's index in the batch.
- An endpoint error or an answer that does not parse is retried up to {config['max_attempts']} times
  (honouring `Retry-After`), then `RewardJudgeError` is raised. A failure never becomes a zero, and
  `reward.PARSE_FAILURES` counts the answers that did not parse.
"""


def build(session: Session, row: Export, version: Version) -> dict[str, Any]:
    params = dict(row.params)
    rubric = rubric_or_refuse(session, params.get("rubric_id"))
    if rubric.content_hash != params["rubric_ref"]["content_sha256"]:
        raise RewardBundleRefusal(
            "The rubric changed after the export was requested; request it again.",
            code="rubric_changed",
        )
    settings = get_settings()
    config = {
        "format": "midataworks.reward-bundle-config/v1",
        "trl_version": settings.reward_bundle_trl_version,
        "judge_model_id": params["judge_model_id"],
        "question": params.get("question"),
        "rubric": {k: v for k, v in params["rubric_ref"].items() if k != "id"},
        "verdict_scores": params["verdict_scores"],
        "sampling": {"temperature": 0, "top_p": 1},
        "seed": int(params.get("seed") or 0),
        "max_attempts": 3,
        "backoff_s": 1.0,
        "timeout_s": 60,
        "base_url_env": BASE_URL_ENV,
        "api_key_env": API_KEY_ENV,
    }
    out = export_dir(row.id)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, out / "reward.py")
    # FTID 015 I1: the bundle's rubric.json comes from the one midataworks.rubric/v1 serialiser.
    rubric_doc = export_rubric_document(rubric)
    atomic_write_bytes(out / "rubric.json", canonical_json(rubric_doc))
    atomic_write_bytes(out / "config.json", canonical_json(config))
    atomic_write_bytes(out / "README.md", _readme(config).encode("utf-8"))
    files = [
        {
            "path": name,
            "role": role,
            "bytes": (out / name).stat().st_size,
            "sha256": file_sha256(out / name),
        }
        for name, role in (
            ("reward.py", "reward_function"),
            ("rubric.json", "rubric"),
            ("config.json", "config"),
            ("README.md", "readme"),
        )
    ]
    items = carry.carried(session, version)
    carried = [{**f, "role": "rule_set"} for f in carry.write_files(items, out)]
    code_items = code_rule_carry.carried(session, version)
    carried += [{**f, "role": "code_rule"} for f in code_rule_carry.write_files(code_items, out)]
    document = _manifest(session, version, row, config, files, items, code_items)
    content = manifest_bytes(document)
    atomic_write_bytes(out / MANIFEST_FILE, content)
    row.files = [*files, *carried]
    row.manifest_sha256 = bytes_sha256(content)
    row.status = ExportStatus.COMPLETED
    row.completed_at = utc_now()
    session.commit()
    return {"status": "completed", "files": files, "manifest_sha256": row.manifest_sha256}


def _manifest(
    session: Session,
    version: Version,
    row: Export,
    config: dict[str, Any],
    files: list[dict[str, Any]],
    rule_sets: list[carry.CarriedRuleSet] | None,
    code_rules: list[code_rule_carry.CarriedCodeRule] | None = None,
) -> dict[str, Any]:
    from ..publishing.publish_job import version_identity

    assembled = assemble(
        session, version, label_column=None, visibility="private", token_scope="write"  # noqa: S106
    )
    outcomes = [o for o in evaluate_checks(assembled.inputs) if o.check != "C-2"]
    rubric = config["rubric"]
    return build_document(
        version=version_identity(version, assembled.dataset.name),
        target={
            "kind": "reward_bundle",
            "dataset_target_type": assembled.dataset.target_type,
            "trl_type": None,
            "trl_version": config["trl_version"],
            "detector_role": None,
            "miforge_set_kind": None,
            "contract_reference": None,
        },
        sources=[manifest_source(f) for f in assembled.sources],
        content={
            "content_kind": "reward_bundle",
            "artifacts": files,
            "rubric": {
                "name": rubric["name"],
                "version": rubric["version"],
                "content_sha256": rubric["content_sha256"],
                "style": rubric["style"],
            },
            "judge_model_id": config["judge_model_id"],
            "trl_version": config["trl_version"],
        },
        caveats=with_held_out_caveat(
            with_session_caveat(caveats_from_outcomes(outcomes), assembled.sources), version.splits
        ),
        publication=None,
        generated_at=utc_now(),
        extensions=carry.extensions_for(assembled.lineage, rule_sets, code_rules=code_rules),
    )

"""Reproduction links: let an imported probe use an evaluation miStudio already recorded
(009 FR-009.77, option (b) of the operator decision of 2026-10-07).

The reproduction gate's target normally comes from a results snapshot (``reproduction.target_for``),
which only ever describes probes trained on data miDataworks SENT. An imported probe trained on
anything else could never get a target, so its plan always refused ``REPRODUCTION_UNAVAILABLE``.

A link names a miDataworks version split and the miStudio probe dataset (view) on which miStudio
recorded an AUROC with a 95% interval for the source probe. The gate trusts that interval only if the
rows really are the same, so the claim is checked HERE, when the link is made, and the checks are
stored with it:

1. **row count** - the rows of our split that map to a class, against miStudio's evaluated rows
   (``n_positive + n_negative`` of the evaluation);
2. **class balance** - positives and negatives separately;
3. **content** - when miStudio serves the view's rows (``GET /api/v1/datasets/{id}/samples``), a
   sha256 over ``[input, class]`` in file order on both sides, and over the sorted lines (AUROC does
   not depend on order). The samples route does not name the split it serves (a multi-split dataset
   gives its ``train`` split), so the served rows are tied to the evaluated view by the view's OWN
   recorded counts first; when they do not reproduce them, or the view is keyword-filtered or holds
   anything but plain text, the content check does NOT run and says why.

Count or balance disagreeing, or a content hash computed and disagreeing: the link is refused
(``link_rows_differ``) and nothing is stored. A content check that did not run: the link is stored as
``counts_only`` and every surface says so. A hash match is never claimed unless both hashes were
computed and are recorded.

How miStudio scored the rows is recorded too (``scoring_form``). The render form is READ from
miStudio at link time, never assumed (2026-10-08 production gate finding): the probe report's
``probe.render_form`` (miStudio ``schemas/probe_monitor.py`` ``ProbeMonitorSummary``, main
``af7cdad2`` onward), falling back to the run's ``environment.render_form`` only when the report does
not carry the field at all. Three cases, stored as recorded:

- ``{"generation_prompt": true, "add_special_tokens": false}`` - the form miLLM serves;
- any other recorded form - an explicit "not served";
- null or absent - NOT RECORDED, which for miStudio means rendered WITHOUT the generation prompt
  (every probe trained before 2026-10-08). Never read as served.

miLLM (``probe_scoring.served_render``, ``ecb88ac``) renders a user-ended input - one user turn of
text, or a chat whose last turn is the user's - WITH the generation prompt and an assistant-ended
chat without it, one BOS from the template. miStudio's served form makes the same decision branch for
branch, so the two are equal BY RENDER RULE. No token ids of the linked rows are available from
miStudio, so the agreement never reads "verified by token ids". A form that renders a row differently
reads "differs" with the reason; the input kinds alone can also show that (miStudio decoded chats,
miDataworks would send text). A column ``chat_json_parser`` turned into messages is sent as those
messages (2026-10-08 live finding 1).

Links stored before the render form was read keep what they stored; ``scoring_form_out`` adds a note
when the stored "no generation prompt" is known to be wrong from the run environment stored with the
link.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...clients import mistudio_client as mc
from ...core.canonical_json import canonical_json, canonical_sha256
from ...core.ids import new_id
from ...models.label_run import LabelRun
from ...models.reproduction_link import ReproductionLink
from . import key_labels
from .errors import DetectorSetError
from .label_rules import value_key
from .set_service import Who, _require_column, _require_completed, _split, _version, split_path

SAMPLES_PAGE_LIMIT = 100
#: Above this many served rows the content check does not run (it reads 1 page per 100 rows).
SAMPLES_ROW_CAP = 50_000
DISTRIBUTION_ROLE = {"in_distribution": "id_test", "out_of_distribution": "ood_eval"}
CLASSES = ("positive", "negative")

#: The form miLLM serves, as miStudio records it (``probe_monitor_render.is_served_render_form`` at
#: miStudio ``af7cdad2``): both fields exactly, ``is True`` / ``is False``.
SERVED_RENDER_FORM = {"generation_prompt": True, "add_special_tokens": False}
NOT_RECORDED = "not recorded - rendered without the generation prompt"
MILLM_RENDER_RULE = (
    "a user-ended input with the generation prompt, an assistant-ended chat without it, one BOS from "
    "the chat template (miLLM probe_scoring.served_render, ecb88ac)"
)
MILLM_RENDER = {
    "text": "each value sent to miLLM's POST /api/probes/score as one user turn, rendered as "
    + MILLM_RENDER_RULE,
    "messages": "each value sent to miLLM's POST /api/probes/score as the chat it holds, rendered as "
    + MILLM_RENDER_RULE,
}


# --- miStudio's side ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Served:
    """The rows miStudio's samples route served for the view's dataset, in its order."""

    rows: list[dict[str, Any]]
    total: int


@dataclass(frozen=True)
class MiStudioSide:
    base_url: str
    probe_id: str
    report: dict[str, Any]
    evaluation: dict[str, Any]
    view: dict[str, Any]
    run: dict[str, Any] | None
    served: Served | None
    served_reason: str | None


def _evaluation(report: Mapping[str, Any], probe_id: str, view_id: str) -> dict[str, Any]:
    evaluated = [e for e in report.get("evaluations") or [] if e.get("dataset_id") == view_id]
    if not evaluated:
        named = sorted(str(e.get("dataset_id")) for e in report.get("evaluations") or [])
        raise DetectorSetError(
            "evaluation_not_found",
            f"miStudio recorded no evaluation of probe {probe_id} on view {view_id}. It evaluated "
            f"it on {', '.join(named) or 'no view at all'}; link one of those.",
            {"mistudio_probe_id": probe_id, "probe_dataset_id": view_id, "evaluated": named},
        )
    found = evaluated[0]
    metrics = found.get("metrics") or {}
    ci = metrics.get("ci") or {}
    if (
        found.get("status") != "completed"
        or metrics.get("auroc") is None
        or ci.get("low") is None
        or ci.get("high") is None
    ):
        raise DetectorSetError(
            "evaluation_not_found",
            f"miStudio's evaluation of probe {probe_id} on view {view_id} is "
            f"{found.get('status')!r} and records no AUROC with an interval, so there is nothing "
            "for the gate to compare with.",
            {"mistudio_probe_id": probe_id, "probe_dataset_id": view_id},
        )
    return dict(found)


def _view_total(view: Mapping[str, Any]) -> int:
    counts = view.get("counts") or {}
    return sum(
        int(counts.get(k) or 0)
        for k in ("positive", "negative", "excluded", "filtered_out", "unparseable")
    )


def content_precondition(view: Mapping[str, Any]) -> str | None:
    """Why the content check cannot run over this view, before any row is read; None when it can."""
    if view.get("keyword_filter"):
        return (
            "miStudio filtered this view by keyword; the filter is not reproduced here, so the "
            "content check did not run."
        )
    kinds = {k: v for k, v in ((view.get("counts") or {}).get("kinds") or {}).items() if v}
    if set(kinds) - {"plain"}:
        return (
            f"miStudio read this view's inputs as {kinds}; the content check covers plain-text "
            "views only, so it did not run."
        )
    total = _view_total(view)
    if total > SAMPLES_ROW_CAP:
        return (
            f"the view holds {total:,} rows, more than the {SAMPLES_ROW_CAP:,} the content check "
            "reads through miStudio's samples route, so it did not run."
        )
    return None


def _served(client: mc.MiStudioClient, view: Mapping[str, Any]) -> tuple[Served | None, str | None]:
    reason = content_precondition(view)
    if reason is not None:
        return None, reason
    rows: list[dict[str, Any]] = []
    total = 0
    page = 1
    try:
        while True:
            body = client.dataset_samples(str(view["dataset_id"]), page, SAMPLES_PAGE_LIMIT)
            rows.extend(dict(item.get("data") or {}) for item in body.get("data") or [])
            pagination = body.get("pagination") or {}
            total = int(pagination.get("total") or 0)
            if total > SAMPLES_ROW_CAP:
                return None, (
                    f"miStudio's samples route serves {total:,} rows for dataset "
                    f"{view['dataset_id']}, more than the {SAMPLES_ROW_CAP:,} the content check "
                    "reads, so it did not run."
                )
            if not pagination.get("has_next"):
                break
            page += 1
    except mc.MiStudioRefused as refused:
        # A clean refusal (the dataset's files are gone, a 400) is a reason, recorded. A dropped
        # connection or a page that is not JSON is NOT: it raises, so a transient failure never
        # quietly makes a weaker, permanent link.
        return None, (
            f"miStudio refused to serve the rows of dataset {view['dataset_id']} ({refused.message} "
            f"{refused.detail!r}), so the content check did not run."
        )
    return Served(rows, total), None


def read_mistudio(client: mc.MiStudioClient, probe_id: str, view_id: str) -> MiStudioSide:
    """Everything the link needs from miStudio (a worker thread: blocking HTTP)."""
    report = client.get_report(probe_id)
    if report is None:
        raise DetectorSetError(
            "evaluation_not_found",
            f"miStudio at {client.base_url} has no probe {probe_id}.",
            {"mistudio_probe_id": probe_id},
        )
    evaluation = _evaluation(report, probe_id, view_id)
    view = next((v for v in client.list_views() if v.get("id") == view_id), None)
    if view is None:
        raise DetectorSetError(
            "evaluation_not_found",
            f"miStudio evaluated probe {probe_id} on view {view_id}, and no longer lists that view.",
            {"probe_dataset_id": view_id},
        )
    run_id = (report.get("probe") or {}).get("run_id")
    run = client.get_run(str(run_id)) if run_id else None
    served, reason = _served(client, view)
    return MiStudioSide(client.base_url, probe_id, report, evaluation, view, run, served, reason)


def candidates(client: mc.MiStudioClient, probe_id: str) -> list[dict[str, Any]]:
    """The views miStudio recorded an AUROC with an interval on for ``probe_id``: what a link can
    name. Each with the view's dataset, split, columns and counts, so a person can find the rows."""
    report = client.get_report(probe_id)
    if report is None:
        return []
    views = {v.get("id"): v for v in client.list_views()}
    out: list[dict[str, Any]] = []
    for e in report.get("evaluations") or []:
        metrics = e.get("metrics") or {}
        ci = metrics.get("ci") or {}
        if metrics.get("auroc") is None or ci.get("low") is None:
            continue
        view = views.get(e.get("dataset_id")) or {}
        out.append(
            {
                "probe_dataset_id": e.get("dataset_id"),
                "view_name": metrics.get("name") or view.get("name"),
                "distribution": view.get("distribution"),
                "auroc": metrics.get("auroc"),
                "ci": [ci.get("low"), ci.get("high")],
                "n_positive": e.get("n_positive"),
                "n_negative": e.get("n_negative"),
                "mistudio_dataset_id": view.get("dataset_id"),
                "split": view.get("split"),
                "config": view.get("config"),
                "input_column": view.get("input_column"),
                "label_column": view.get("label_column"),
                "label_mapping": view.get("label_mapping"),
                "input_kinds": (view.get("counts") or {}).get("kinds"),
            }
        )
    return out


# --- the checks (pure) ---------------------------------------------------------------------------


def mistudio_map_label(value: Any, mapping: Mapping[str, str]) -> str | None:
    """miStudio's ``map_label`` (``probe_monitor_inputs.py`` at ``c829a2cc``), so the served rows are
    read the way miStudio read them."""
    if value is None:
        return mapping.get("None") or mapping.get("null")
    key = str(value)
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    if lowered in mapping:
        return mapping[lowered]
    if isinstance(value, bool) or lowered in ("true", "false"):
        alternative = "1" if lowered == "true" else "0"
        if alternative in mapping:
            return mapping[alternative]
    return None


def content_hashes(rows: Sequence[tuple[Any, str]]) -> dict[str, str]:
    """sha256 of the canonical JSON of the ``[input, class]`` list in the given order, and of the
    same list sorted by each item's canonical bytes (the one canonical serialiser, ADR-005)."""
    items = [[v, c] for v, c in rows]
    return {
        "ordered": canonical_sha256(items),
        "unordered": canonical_sha256(sorted(items, key=canonical_json)),
    }


def served_evaluated_rows(served: Served, view: Mapping[str, Any]) -> list[tuple[Any, str]]:
    """The served rows miStudio would have evaluated: mapped to a class by the VIEW's mapping, with
    an input miStudio could parse as plain text (non-empty after stripping)."""
    mapping = {str(k): str(v) for k, v in (view.get("label_mapping") or {}).items()}
    out: list[tuple[Any, str]] = []
    for row in served.rows:
        cls = mistudio_map_label(row.get(str(view.get("label_column"))), mapping)
        if cls not in CLASSES:
            continue
        value = row.get(str(view.get("input_column")))
        if not isinstance(value, str) or not value.strip():
            continue
        out.append((value, cls))
    return out


def run_checks(
    ours: Sequence[tuple[Any, str]],
    evaluation: Mapping[str, Any],
    view: Mapping[str, Any],
    served: Served | None,
    served_reason: str | None,
) -> dict[str, Any]:
    """Every check, and the verdict: ``content``, ``counts_only`` or ``refused``."""
    ours_pos = sum(1 for _, c in ours if c == "positive")
    ours_neg = len(ours) - ours_pos
    ms_pos = int(evaluation.get("n_positive") or 0)
    ms_neg = int(evaluation.get("n_negative") or 0)
    row_count = {
        "ran": True,
        "ours": len(ours),
        "mistudio": ms_pos + ms_neg,
        "passed": len(ours) == ms_pos + ms_neg,
    }
    balance = {
        "ran": True,
        "ours": {"positive": ours_pos, "negative": ours_neg},
        "mistudio": {"positive": ms_pos, "negative": ms_neg},
        "passed": ours_pos == ms_pos and ours_neg == ms_neg,
    }
    content: dict[str, Any] = {"ran": False, "reason": served_reason, "order": "file order"}
    if served is not None:
        theirs = served_evaluated_rows(served, view)
        theirs_pos = sum(1 for _, c in theirs if c == "positive")
        tie = {
            "served_rows": served.total,
            "view_rows": _view_total(view),
            "served_positive": theirs_pos,
            "served_negative": len(theirs) - theirs_pos,
        }
        if (
            served.total != _view_total(view)
            or theirs_pos != ms_pos
            or len(theirs) - theirs_pos != ms_neg
        ):
            content = {
                "ran": False,
                "order": "file order",
                "served": tie,
                "reason": (
                    f"miStudio's samples route served {served.total:,} rows giving "
                    f"{theirs_pos:,} positive and {len(theirs) - theirs_pos:,} negative under the "
                    f"view's mapping; the evaluated view recorded {_view_total(view):,} rows, "
                    f"{ms_pos:,} positive and {ms_neg:,} negative. The route serves a multi-split "
                    "dataset's train split and does not name it, so these may be other rows; the "
                    "content check did not run."
                ),
            }
        else:
            mine = content_hashes(ours)
            other = content_hashes(theirs)
            content = {
                "ran": True,
                "order": "file order",
                "served": tie,
                "reason": None,
                "ours_sha256": mine,
                "mistudio_sha256": other,
                "ordered_match": mine["ordered"] == other["ordered"],
                "unordered_match": mine["unordered"] == other["unordered"],
                "passed": mine["unordered"] == other["unordered"],
            }
    if not row_count["passed"] or not balance["passed"]:
        level = "refused"
    elif content["ran"]:
        level = "content" if content["passed"] else "refused"
    else:
        level = "counts_only"
    return {"row_count": row_count, "class_balance": balance, "content": content, "level": level}


def _form_text(form: Any) -> str:
    return canonical_json(form).decode("utf-8")


def is_served_render_form(recorded: Any) -> bool:
    """miStudio's own rule (``probe_monitor_render.is_served_render_form``): a RECORDED form whose
    ``generation_prompt`` is True and ``add_special_tokens`` is False. None (not recorded) is not.
    """
    return (
        isinstance(recorded, Mapping)
        and recorded.get("generation_prompt") is True
        and recorded.get("add_special_tokens") is False
    )


def read_render_form(
    probe: Mapping[str, Any] | None, run: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The render form miStudio recorded for the probe, as recorded.

    The probe report's ``render_form`` first; the run's ``environment.render_form`` only when the
    report does not carry the field at all (a miStudio older than ``af7cdad2``). A null or absent
    form is NOT RECORDED - never the served form. miStudio's own ``render_served`` is kept beside our
    reading; when it disagrees with the rule the form is not called served."""
    probe = probe or {}
    env = (run or {}).get("environment") or {}
    if "render_form" in probe:
        form, source = probe.get("render_form"), "probe"
    elif env.get("render_form") is not None:
        form, source = env.get("render_form"), "run"
    else:
        form, source = None, None
    reported = probe.get("render_served")
    reported = reported if isinstance(reported, bool) else None
    recorded = form is not None
    served = is_served_render_form(form) and reported is not False
    return {
        "recorded": recorded,
        "form": form if recorded else None,
        "source": source if recorded else None,
        "served": served,
        "mistudio_render_served": reported,
    }


def last_roles(rows: Sequence[tuple[Any, str]]) -> dict[str, int]:
    """How many of our rows end on a user turn and how many on an assistant turn. A text value is
    one user turn; a chat ends on its last message's role (``other`` for anything else)."""
    out = {"user": 0, "assistant": 0, "other": 0}
    for value, _ in rows:
        if isinstance(value, list):
            last = value[-1] if value else None
            role = last.get("role") if isinstance(last, Mapping) else None
            out[role if role in ("user", "assistant") else "other"] += 1
        else:
            out["user"] += 1
    return out


def _render_description(render: Mapping[str, Any], kinds: Mapping[str, int]) -> str:
    rows = (
        "each plain-text row parsed as one user turn"
        if set(kinds) <= {"plain"}
        else f"inputs read as {dict(kinds)}"
    )
    if not render["recorded"]:
        return (
            f"{rows}, rendered with the model's chat template; render form {NOT_RECORDED} "
            "(miStudio records no render_form for probes trained before 2026-10-08)"
        )
    form = _form_text(render["form"])
    if render["served"]:
        return (
            f"{rows}, rendered with the model's chat template WITH the generation prompt after a "
            "user turn and one BOS from the template (add_special_tokens false): the form miLLM "
            f"serves (render_form {form}, recorded on the miStudio {render['source']})"
        )
    return (
        f"{rows}, rendered with the model's chat template under render_form {form} (recorded on "
        f"the miStudio {render['source']}), which is not the form miLLM serves"
    )


def _render_agreement(
    render: Mapping[str, Any], roles: Mapping[str, int], scope: str
) -> tuple[str, str]:
    """Does miStudio's render form render our rows as miLLM will? Per row: an assistant-ended chat
    is rendered without the generation prompt on both sides; a user-ended one with it by miLLM and
    by miStudio only under ``generation_prompt: true``; ``add_special_tokens: true`` adds a second
    BOS miLLM never adds."""
    tail = (
        f" Scored over scope {scope!r}. No token ids of these rows are available from miStudio, so "
        "the two are not compared token by token: this is not verified by token ids."
    )
    if render["served"]:
        return (
            "equal_by_render_rule",
            "miStudio recorded the served render form and miLLM renders by the same rule, so "
            "each row is the same token sequence by construction." + tail,
        )
    user_ended = int(roles.get("user", 0)) + int(roles.get("other", 0))
    assistant_ended = int(roles.get("assistant", 0))
    if not render["recorded"]:
        if user_ended:
            return (
                "differs",
                f"miStudio's render form is {NOT_RECORDED}; miLLM renders a user-ended input WITH "
                f"it, and {user_ended:,} of the {user_ended + assistant_ended:,} rows end on a "
                "user turn, so miLLM scores a longer sequence than miStudio did." + tail,
            )
        return (
            "not_verified",
            f"miStudio's render form is {NOT_RECORDED}. Every row ends on an assistant turn, which "
            "both render without the generation prompt, but miStudio recorded no BOS handling, so "
            "the two are not known to be the same token sequence." + tail,
        )
    form = render["form"] if isinstance(render["form"], Mapping) else {}
    why: list[str] = []
    if form.get("add_special_tokens") is not False:
        why.append(
            f"add_special_tokens is {form.get('add_special_tokens')!r} (miLLM adds no BOS beyond "
            "the template's)"
        )
    if user_ended and form.get("generation_prompt") is not True:
        why.append(
            f"generation_prompt is {form.get('generation_prompt')!r} and {user_ended:,} rows end on "
            "a user turn, which miLLM renders WITH the generation prompt"
        )
    if why:
        return (
            "differs",
            f"miStudio recorded render_form {_form_text(render['form'])}: "
            + "; ".join(why)
            + "."
            + tail,
        )
    return (
        "equal_by_render_rule",
        f"miStudio recorded render_form {_form_text(render['form'])}, not the served form, but "
        "every row ends on an assistant turn, which both render without the generation prompt and "
        "with one BOS." + tail,
    )


def scoring_form(
    view: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    ours_form: str,
    probe: Mapping[str, Any] | None = None,
    roles: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """How miStudio scored the rows, how miLLM will, and whether they are known to be the same.

    ``probe`` is the report's ``probe`` block (its ``render_form``); ``roles`` is ``last_roles`` of
    our rows (a text column: every row user-ended)."""
    kinds = {k: v for k, v in ((view.get("counts") or {}).get("kinds") or {}).items() if v}
    env = (run or {}).get("environment") or {}
    config = (run or {}).get("config") or {}
    scope = env.get("scope") or config.get("scope") or "not reported"
    render = read_render_form(probe, run)
    if roles is None:
        roles = {"user": 1, "assistant": 0, "other": 0}
    mistudio = {
        "input_kinds": kinds,
        "scope": scope,
        "template_hash": env.get("template_hash") or "not reported",
        "max_length": env.get("max_length") or config.get("max_length") or "not reported",
        "render_form": render["form"],
        "render_form_recorded": render["recorded"],
        "render_form_source": render["source"],
        "render_served": render["served"],
        "mistudio_render_served": render["mistudio_render_served"],
        "described_as": _render_description(render, kinds),
    }
    millm = {"input_form": ours_form, "described_as": MILLM_RENDER[ours_form], "last_roles": roles}
    if ours_form == "text" and set(kinds) - {"plain"}:
        agreement, why = (
            "differs",
            (
                f"miStudio read these rows as {kinds} (chats it decoded); miDataworks would send each "
                "value to miLLM as one user turn of text."
            ),
        )
    elif ours_form == "messages" and "plain" in kinds:
        agreement, why = (
            "differs",
            (
                "miStudio read these rows as plain text; miDataworks would send each value to miLLM "
                "as a chat."
            ),
        )
    else:
        # The same rows on both sides (one user turn each, or the chat each holds: a parsed
        # messages column against chats miStudio decoded); the render form decides.
        agreement, why = _render_agreement(render, roles, scope)
    return {
        "mistudio": mistudio,
        "millm": millm,
        "agreement": agreement,
        "token_ids_compared": False,
        "reason": why,
    }


def scoring_form_out(stored: Mapping[str, Any] | None, evidence: Mapping[str, Any] | None) -> Any:
    """A link's stored ``scoring_form`` as stored, with a ``note`` when it predates reading the
    render form AND the run environment stored with the link shows its "no generation prompt"
    description is wrong. History is never rewritten; only that note is added."""
    if not stored or "render_form_recorded" in (stored.get("mistudio") or {}):
        return stored
    env = (evidence or {}).get("run_environment") or {}
    if not is_served_render_form(env.get("render_form")):
        return stored
    return {
        **stored,
        "note": (
            "This description was stored before miDataworks read miStudio's render form, and says "
            "miStudio rendered without the generation prompt. The run environment stored with this "
            f"link records render_form {_form_text(env['render_form'])}, the form miLLM "
            "serves, so that description is wrong for this probe. Link the split again to record "
            "the render form and its agreement."
        ),
    }


# --- our side and the record ---------------------------------------------------------------------


def our_rows(
    session: Session,
    version_id: str,
    split: str,
    input_column: str,
    label_column: str,
    label_mapping: Mapping[str, str],
) -> tuple[list[tuple[Any, str]], str, list[str]]:
    """``(input, class)`` for every row that maps to a class, in file order - exactly the rows
    ``reproduction.target_rows`` will score - the input form (``text`` or ``messages``), and each
    row's key in the same order (for ``key_labels.summary``)."""
    version = _version(session, version_id)
    _require_completed(version)
    _split(version, split)
    _require_column(version, input_column, "input column")
    _require_column(version, label_column, "label column")
    table = pq.read_table(
        split_path(version, split), columns=[input_column, label_column, "_dw_row_key"]
    )
    mapping = {value_key(k): v for k, v in label_mapping.items()}
    rows: list[tuple[Any, str]] = []
    keys: list[str] = []
    form = "text"
    for value, raw, key in zip(
        table.column(0).to_pylist(),
        table.column(1).to_pylist(),
        table.column(2).to_pylist(),
        strict=True,
    ):
        cls = mapping.get(value_key(raw)) if raw is not None else None
        if cls not in CLASSES:
            continue
        if isinstance(value, list):
            form = "messages"
        rows.append((value, cls))
        keys.append(str(key))
    return rows, form, keys


def create(
    session: Session,
    data: Mapping[str, Any],
    side: MiStudioSide,
    who: Who,
    approval: tuple[str, str] | None,
) -> ReproductionLink:
    """Check the claim and store the link, or refuse ``link_rows_differ`` naming every check."""
    view = side.view
    role = DISTRIBUTION_ROLE.get(str(view.get("distribution")))
    if view.get("role") != "eval" or role is None:
        raise DetectorSetError(
            "role_invalid",
            f"miStudio's view {view.get('id')} is a {view.get('role')!r} view with distribution "
            f"{view.get('distribution')!r}; the gate reproduces an evaluation view marked "
            "in_distribution or out_of_distribution.",
            {"probe_dataset_id": view.get("id")},
        )
    input_column = str(data.get("input_column") or view.get("input_column"))
    label_column = str(data.get("label_column") or view.get("label_column"))
    mapping = data.get("label_mapping") or view.get("label_mapping") or {}
    ours, form, keys = our_rows(
        session, data["version_id"], data["split"], input_column, label_column, mapping
    )
    checks = run_checks(ours, side.evaluation, view, side.served, side.served_reason)
    # 2026-10-08 finding 2: the link states the rows AND the distinct keys, the same count the
    # label run and the gate state, and names any key whose copies carry both classes. It does not
    # refuse on them: miStudio evaluated every copy, so its interval is over every copy.
    checks["row_keys"] = {
        "ran": True,
        **key_labels.summary(zip(keys, (c for _, c in ours), strict=True)),
        "passed": True,
    }
    metrics = side.evaluation["metrics"]
    if checks["level"] == "refused":
        raise DetectorSetError(
            "link_rows_differ",
            refusal_message(checks),
            {"checks": checks, "mistudio_probe_id": side.probe_id, "probe_dataset_id": view["id"]},
        )
    row = ReproductionLink(
        id=new_id("rpl"),
        mistudio_base_url=side.base_url,
        mistudio_probe_id=side.probe_id,
        mistudio_run_id=(side.report.get("probe") or {}).get("run_id"),
        probe_dataset_id=str(view["id"]),
        evaluation_id=side.evaluation.get("id"),
        view_name=metrics.get("name") or view.get("name"),
        role=role,
        version_id=str(data["version_id"]),
        split=str(data["split"]),
        input_column=str(input_column),
        label_column=str(label_column),
        label_mapping={str(k): str(v) for k, v in mapping.items()},
        auroc=float(metrics["auroc"]),
        ci_low=float(metrics["ci"]["low"]),
        ci_high=float(metrics["ci"]["high"]),
        n_positive=int(side.evaluation.get("n_positive") or 0),
        n_negative=int(side.evaluation.get("n_negative") or 0),
        check_level=checks["level"],
        checks=checks,
        scoring_form=scoring_form(view, side.run, form, side.report.get("probe"), last_roles(ours)),
        evidence={
            "report_sha256": canonical_sha256(side.report),
            "view": view,
            "evaluation": {k: v for k, v in side.evaluation.items() if k != "metrics"}
            | {"metrics": {k: v for k, v in metrics.items() if k not in ("roc", "length_bands")}},
            "run_environment": (side.run or {}).get("environment"),
        },
        created_by=who.who,
        created_by_origin=who.origin,
        approval_id=approval[0] if approval else None,
        approved_by=approval[1] if approval else None,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.execute(
            select(ReproductionLink).where(
                ReproductionLink.mistudio_base_url == side.base_url,
                ReproductionLink.mistudio_probe_id == side.probe_id,
                ReproductionLink.probe_dataset_id == str(view["id"]),
                ReproductionLink.version_id == str(data["version_id"]),
                ReproductionLink.split == str(data["split"]),
            )
        ).scalar_one_or_none()
        raise DetectorSetError(
            "link_exists",
            "This version split is already linked to that miStudio evaluation"
            + (f" ({existing.id})." if existing else "."),
            {"link_id": existing.id if existing else None},
        ) from None
    return row


def refusal_message(checks: Mapping[str, Any]) -> str:
    parts: list[str] = []
    rc, bal, content = checks["row_count"], checks["class_balance"], checks["content"]
    if not rc["passed"]:
        parts.append(
            f"{rc['ours']:,} rows of the split map to a class, and miStudio evaluated "
            f"{rc['mistudio']:,}"
        )
    if not bal["passed"]:
        parts.append(
            f"the split has {bal['ours']['positive']:,} positive and {bal['ours']['negative']:,} "
            f"negative, miStudio {bal['mistudio']['positive']:,} and "
            f"{bal['mistudio']['negative']:,}"
        )
    if content.get("ran") and not content.get("passed"):
        parts.append("the input texts hash differently from the rows miStudio served")
    return (
        "These are not the rows miStudio evaluated: "
        + "; ".join(parts)
        + ". The link is refused and nothing was stored; link the split that holds those rows."
    )


def used_by(session: Session, link_id: str) -> list[str]:
    return list(
        session.execute(
            select(LabelRun.id).where(
                LabelRun.endpoint_snapshot["reproduction"]["link_id"].astext == link_id
            )
        ).scalars()
    )


def get(session: Session, link_id: str) -> ReproductionLink:
    row = session.get(ReproductionLink, link_id)
    if row is None:
        raise DetectorSetError("link_not_found", f"No reproduction link {link_id}.")
    return row


def list_links(session: Session, mistudio_probe_id: str | None = None) -> list[ReproductionLink]:
    query = select(ReproductionLink).order_by(ReproductionLink.created_at.desc())
    if mistudio_probe_id:
        query = query.where(ReproductionLink.mistudio_probe_id == mistudio_probe_id)
    return list(session.execute(query).scalars())


def delete(session: Session, link_id: str) -> None:
    row = get(session, link_id)
    runs = used_by(session, link_id)
    if runs:
        raise DetectorSetError(
            "link_in_use",
            f"Label run(s) {', '.join(runs)} used link {link_id}; a used link is the evidence their "
            "reproduction records point at, so it is never deleted.",
            {"label_run_ids": runs},
        )
    session.delete(row)
    session.commit()


def link_out(session: Session, row: ReproductionLink) -> dict[str, Any]:
    return {
        "id": row.id,
        "mistudio_base_url": row.mistudio_base_url,
        "mistudio_probe_id": row.mistudio_probe_id,
        "mistudio_run_id": row.mistudio_run_id,
        "probe_dataset_id": row.probe_dataset_id,
        "evaluation_id": row.evaluation_id,
        "view_name": row.view_name,
        "role": row.role,
        "version_id": row.version_id,
        "split": row.split,
        "input_column": row.input_column,
        "label_column": row.label_column,
        "label_mapping": row.label_mapping,
        "mistudio_auroc": row.auroc,
        "mistudio_ci": [row.ci_low, row.ci_high],
        "n_positive": row.n_positive,
        "n_negative": row.n_negative,
        "check_level": row.check_level,
        "checks": row.checks,
        "scoring_form": scoring_form_out(row.scoring_form, row.evidence),
        "evidence": {"report_sha256": row.evidence.get("report_sha256")},
        "created_by": row.created_by,
        "created_by_origin": row.created_by_origin,
        "approval_id": row.approval_id,
        "approved_by": row.approved_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "used_by_label_runs": used_by(session, row.id),
    }

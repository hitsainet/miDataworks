"""The dataset card (FR-008.7–FR-008.10, FR-008.63; FTID 008 section 3.4).

Three parts, assembled as ``front matter + prose + record``:

- the **front matter** (``license``, ``task_categories``, ``pretty_name``, ``configs``) is generated
  from the manifest on every publish and checked by a YAML round trip;
- the **prose** is the operator's free text (title, summary, added sections). It is the only part
  an edit can change. :func:`clean_prose` strips anything in it that tries to be front matter or a
  record section, so an edit can never replace the ``configs`` block or delete a caveat;
- the **record section**, between :data:`RECORD_START` and :data:`RECORD_END`, lists every
  FR-008.7 fact, the caveats, a digest table and the "Versions in this repository" history, which
  is merged by version ID from the head README and never shortened (FR-008.9).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from .checks import CheckOutcome, Outcome
from .licence_table import TABLE

RECORD_START = "<!-- dw:record — regenerated on every publish -->"
RECORD_END = "<!-- dw:record-end -->"
HISTORY_HEADING = "### Versions in this repository"
EVALUATION_ONLY = (
    "**Evaluation only.** At least one source's terms forbid redistribution for training; use "
    "these rows to evaluate, not to train."
)

_FRONT = re.compile(r"\A\s*---\n.*?\n---\s*\n?", re.DOTALL)


class CardError(ValueError):
    pass


# --- front matter -----------------------------------------------------------------------------


def front_licence(manifest: Mapping[str, Any]) -> str:
    """One Hub licence id when every source states the same permitted id; otherwise ``other``."""
    ids: set[str] = set()
    for source in manifest["sources"]:
        lic = source["licence"]
        raw = lic["raw"]
        if lic["licence_class"] != "permits_redistribution" or not isinstance(raw, str):
            return "other"
        ids.add(raw.strip().lower())
    if len(ids) == 1:
        only = ids.pop()
        if only in TABLE.permits:
            return only
    return "other"


def task_categories(manifest: Mapping[str, Any]) -> list[str]:
    content = manifest["content"]
    labelled = content["content_kind"] == "rows" and content["projection"]["label_column"]
    target = manifest["target"]["dataset_target_type"]
    if target == "detector" or labelled:
        return ["text-classification"]
    return ["text-generation"]


def front_matter(manifest: Mapping[str, Any]) -> dict[str, Any]:
    version = manifest["version"]
    splits = manifest["content"]["splits"]
    return {
        "license": front_licence(manifest),
        "task_categories": task_categories(manifest),
        "pretty_name": f"{version['dataset']} v{version['number']}",
        "configs": [
            {
                "config_name": "default",
                "data_files": [{"split": s["name"], "path": s["path"]} for s in splits],
            }
        ],
    }


# --- record section ---------------------------------------------------------------------------


def _source_line(source: Mapping[str, Any]) -> str:
    origin = source["origin"]
    lic = source["licence"]
    if origin["kind"] == "hub":
        where = f"`{origin['repo_id']}` @ `{origin['revision'][:8]}`"
    else:
        where = f"upload `{origin['filename']}` (SHA-256 `{origin['content_sha256'][:12]}`)"
    return (
        f"- {where} — licence {lic['displayed']} ({lic['licence_class'].replace('_', ' ')}, "
        f"licence table v{lic['table_version']})"
    )


def _labeler_lines(content: Mapping[str, Any]) -> list[str]:
    if not content["labelers"]:
        return ["No model labeler; labels, if any, came with the sources."]
    lines: list[str] = []
    for lab in content["labelers"]:
        model = lab["model_id"] or "not stated"
        revision = lab["model_revision"] or "not reported"
        lines.append(f"- {lab['role']} `{model}` (revision `{revision}`)")
        if lab["question"]:
            lines.append(f"  - question: *{lab['question']}*")
        if lab["template"]:
            t = lab["template"]
            lines.append(f"  - template {t['name']} v{t['version']} (`{t['content_sha256'][:12]}`)")
        rubric = (lab.get("extensions") or {}).get("rubric")
        if rubric:
            lines.append(
                f"  - rubric {rubric['name']} v{rubric['version']} "
                f"(`{str(rubric['content_sha256'])[:12]}`)"
            )
        if lab.get("run_ids"):
            lines.append(f"  - label run(s): {', '.join(f'`{r}`' for r in lab['run_ids'])}")
        if lab["thresholds"]:
            th = lab["thresholds"]
            lines.append(
                f"  - positive at or above {th['positive_at_or_above']}, negative at or below "
                f"{th['negative_at_or_below']}"
            )
        if lab["excluded_share"] is not None:
            lines.append(f"  - the exclusion band removed {lab['excluded_share']:.1%} of rows")
    return lines


def _generated_lines(lineage: Mapping[str, Any], total_rows: int) -> list[str]:
    """Which model wrote which rows (C-4's facts), from the record's lineage."""
    generated = int(lineage.get("generated_rows") or 0)
    generators = list(lineage.get("generators") or [])
    if not generated and not generators:
        return ["No row was generated by a model."]
    lines = [f"{generated:,} of {total_rows:,} row(s) were generated by a model."]
    for g in generators:
        revision = g.get("revision") or "not reported"
        lines.append(
            f"- `{g.get('model_id') or 'not stated'}` (revision `{revision}`), generation run "
            f"`{g['generation_run_id']}`" + (f" ({g['mode']})" if g.get("mode") else "")
        )
    if generated and not generators:
        lines.append("- no generation run is bound in this version's lineage; the model is unknown")
    return lines


def _calibration_lines(content: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for cal in content["calibration"]:
        if cal["status"] != "recorded":
            lines.append(f"- `{cal['labeler_fingerprint'][:12]}`: no calibration recorded")
            continue
        auroc = cal["auroc"]
        numbers = (
            f"AUROC {auroc['value']:.3f} (95% CI {auroc['ci_low']:.3f}–{auroc['ci_high']:.3f}, "
            f"n={auroc['n']:,})"
            if auroc
            else "no AUROC"
        )
        cited = ""
        if cal["calibration_set"]:
            cited = f"; calibration set {cal['calibration_set']['id']} is cited, its rows are not shipped"
        lines.append(f"- record `{cal['record_id']}`: verdict {cal['verdict']}, {numbers}{cited}")
    return lines


def _history_rows(history: Sequence[Mapping[str, str]]) -> list[str]:
    rows = ["| version | commit | date |", "|---|---|---|"]
    for entry in history:
        rows.append(f"| {entry['version']} | `{entry['commit']}` | {entry['date']} |")
    return rows


def record_section(
    manifest: Mapping[str, Any],
    outcomes: Sequence[CheckOutcome],
    *,
    drop_summary: Sequence[Mapping[str, Any]],
    digests: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, str]],
) -> str:
    """Every FR-008.7 fact. ``digests`` are ``{path, bytes, sha256}`` for each published file the
    card can name (it cannot name its own hash); ``history`` is already merged."""
    version = manifest["version"]
    content = manifest["content"]
    out: list[str] = [RECORD_START, "", "## Record", ""]
    if any(s["licence"]["licence_class"] == "forbids_redistribution" for s in manifest["sources"]):
        out += [EVALUATION_ONLY, ""]
    out += [
        f"Version {version['number']} of `{version['dataset']}` (`{version['version_id']}`), "
        f"recipe `{version['recipe']['sha256']}`, seed {version['seed']}, row keys "
        f"`{version['row_key_scheme']}`.",
        "",
        "### Sources",
        "",
        *[_source_line(s) for s in manifest["sources"]],
        "",
        "### Labels",
        "",
        *_labeler_lines(content),
    ]
    lineage = (manifest.get("extensions") or {}).get("lineage")
    if lineage is not None:
        total = sum(int(s["rows"]) for s in content["splits"])
        out += ["", "### Generated rows", "", *_generated_lines(lineage, total)]
    calibration = _calibration_lines(content)
    if calibration:
        out += ["", "### Calibration", "", *calibration]
    projection = content["projection"]
    out += [
        "",
        f"Review overrides applied: {projection['overrides_applied']:,}; rows omitted as excluded "
        f"{projection['omitted_excluded']:,} and as flagged and unresolved "
        f"{projection['omitted_flagged_unresolved']:,}.",
        "",
        "### Rows dropped by step",
        "",
    ]
    ancestors = list((lineage or {}).get("ancestor_versions") or [])
    if ancestors:
        out += [
            "This table covers this version's own steps only. Rows were also dropped, generated "
            "and labelled in its ancestor versions, each of which records its own steps: "
            + ", ".join(f"v{a['number']} (`{a['version_id']}`)" for a in ancestors)
            + ".",
            "",
        ]
    if drop_summary:
        out += ["| step | operator | dropped | rows out |", "|---|---|---|---|"]
        for d in drop_summary:
            out.append(
                f"| {d['step_index']} | {d['operator']} {d['operator_version']} | "
                f"{int(d['dropped']):,} | {int(d['rows_out']):,} |"
            )
    else:
        out.append("No step dropped rows.")
    out += ["", "### Splits", "", "| split | rows | labels | held out |", "|---|---|---|---|"]
    for s in content["splits"]:
        labels = ", ".join(f"{k}: {v:,}" for k, v in s["label_counts"].items()) or "—"
        out.append(
            f"| {s['name']} | {s['rows']:,} | {labels} | {'yes' if s['held_out'] else 'no'} |"
        )
    out += ["", "### Caveats", ""]
    caveats = list(manifest["caveats"])
    if caveats:
        out += [f"- **{c['severity']}** ({c['code']}): {c['message']}" for c in caveats]
    else:
        out.append("None recorded.")
    out += ["", "### Checks at publish", ""]
    for o in outcomes:
        out.append(f"- {o.check} {o.outcome.value}: {o.reason}")
    out += [
        "",
        "### Files",
        "",
        "| path | bytes | SHA-256 |",
        "|---|---|---|",
        *[f"| `{d['path']}` | {int(d['bytes']):,} | `{d['sha256']}` |" for d in digests],
        "",
        HISTORY_HEADING,
        "",
        *_history_rows(history),
        "",
        RECORD_END,
    ]
    return "\n".join(out)


# --- history ----------------------------------------------------------------------------------

_HISTORY_ROW = re.compile(
    r"^\|\s*(?P<version>[^|`]+?)\s*\|\s*`(?P<commit>[^`]+)`\s*\|\s*(?P<date>[^|]+?)\s*\|$"
)


def parse_history(readme: str | None) -> list[dict[str, str]]:
    """The "Versions in this repository" rows of a head README, in order."""
    if not readme or HISTORY_HEADING not in readme:
        return []
    after = readme.split(HISTORY_HEADING, 1)[1]
    rows: list[dict[str, str]] = []
    for line in after.splitlines():
        line = line.strip()
        if line.startswith(RECORD_END) or line.startswith("#"):
            break
        match = _HISTORY_ROW.match(line)
        if match:
            rows.append(match.groupdict())
    return rows


#: A card cannot name the commit it is part of; the current version's row says this, and the
#: next publish resolves it from the publication record.
THIS_COMMIT = "this commit"


def merge_history(
    previous: Sequence[Mapping[str, str]],
    entry: Mapping[str, str],
    recorded_commits: Mapping[str, str],
) -> list[dict[str, str]]:
    """Merged by version ID and never shortened (FR-008.9).

    Earlier rows that said ``this commit`` take the commit the publication record holds for that
    version; the current version keeps its first row (so republishing identical content yields an
    identical card, and "no change" is detectable).
    """
    merged: list[dict[str, str]] = []
    for row in previous:
        out = dict(row)
        if out["commit"] == THIS_COMMIT and out["version"] != entry["version"]:
            out["commit"] = recorded_commits.get(out["version"], THIS_COMMIT)
        merged.append(out)
    if not any(r["version"] == entry["version"] for r in merged):
        merged.append(dict(entry))
    return merged


# --- prose and assembly -----------------------------------------------------------------------


def clean_prose(prose: str) -> str:
    """The operator's prose with any front matter and any record section removed (FR-008.8)."""
    text = _FRONT.sub("", prose.replace("\r\n", "\n"), count=1)
    if RECORD_START in text:
        head, _, tail = text.partition(RECORD_START)
        text = head + (tail.split(RECORD_END, 1)[1] if RECORD_END in tail else "")
    text = text.replace(RECORD_END, "")
    return text.strip()


def default_prose(manifest: Mapping[str, Any]) -> str:
    version = manifest["version"]
    splits = manifest["content"]["splits"]
    sizes = " · ".join(f"{s['name']} {s['rows']:,}" for s in splits)
    return (
        f"# {version['dataset']} v{version['number']}\n\n"
        f"Built with miDataworks ({sizes} rows). Describe what the rows are and how to use them."
    )


def assemble(fm: Mapping[str, Any], prose: str, record: str) -> bytes:
    """``---\\n<yaml>---\\n\\n<prose>\\n\\n<record>\\n`` as UTF-8, after a YAML round trip."""
    dumped = yaml.safe_dump(dict(fm), sort_keys=False, allow_unicode=True)
    if yaml.safe_load(dumped) != dict(fm):
        raise CardError("the front matter does not survive a YAML round trip")
    text: str = "---\n" + dumped + "---\n\n" + clean_prose(prose) + "\n\n" + record + "\n"
    return text.encode("utf-8")


def read_front_matter(card: bytes | str) -> dict[str, Any]:
    text = card.decode("utf-8") if isinstance(card, bytes) else card
    match = _FRONT.match(text)
    if match is None:
        raise CardError("the card has no front matter")
    block = match.group(0).strip()[3:-3]
    data = yaml.safe_load(block)
    if not isinstance(data, dict):
        raise CardError("the front matter is not a mapping")
    return data


def caveats_from_outcomes(outcomes: Sequence[CheckOutcome]) -> list[dict[str, Any]]:
    """Amber checks and notes become manifest caveats (M-13) with the copy rule's next step."""
    codes = {
        "N-failing_verdict": "failing_verdict",
        "N-unpinned_labeler": "unpinned_labeler",
        "N-revision_not_reported": "revision_not_reported",
        "N-licence_changed_since_build": "licence_changed_since_build",
        "N-diversity_falling": "diversity_falling",
    }
    out: list[dict[str, Any]] = []
    for o in outcomes:
        if o.outcome is Outcome.AMBER:
            code = (
                "shortcut_warning"
                if o.check == "C-7" and not o.evidence.get("not_checked")
                else "amber_check"
            )
            out.append(
                {
                    "code": code,
                    "severity": "amber",
                    "message": f"{o.check}: {o.reason} {o.next_step}",
                    "detail": {"check": o.check, **o.evidence},
                }
            )
        elif o.outcome is Outcome.NOTE and o.check in codes:
            out.append(
                {
                    "code": codes[o.check],
                    "severity": "note",
                    "message": o.reason,
                    "detail": dict(o.evidence),
                }
            )
    return out

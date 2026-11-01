"""A judge reward function for TRL's GRPO trainer, written by miDataworks (008 FR-008.46 - 008.49).

Standard library only. It reads ``config.json`` and ``rubric.json`` beside this file, renders the
rubric for each completion, asks the judge (an OpenAI-compatible chat endpoint) with deterministic
settings and a recorded seed, parses the answer exactly as miDataworks does, and maps the verdict to
a number with the bundle's ``verdict_scores``.

The endpoint and key are read from environment variables named in ``config.json`` (by default
``MIDATAWORKS_JUDGE_BASE_URL`` and ``MIDATAWORKS_JUDGE_API_KEY``). No URL, key or secret is written
into this bundle.

On an endpoint error or an answer that does not parse it retries (honouring ``Retry-After``) up to
``max_attempts`` times, then RAISES :class:`RewardJudgeError`. It never returns a silent zero, and it
does not return ``None`` for a failure: TRL reads ``None`` as "not applicable", and a failure is not
that. ``PARSE_FAILURES`` counts every answer that did not parse, retried or not.

A reward score is not an evaluation result. Evaluation needs a separate held-out detector or judge.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONFIG: dict[str, Any] = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
RUBRIC: dict[str, Any] = json.loads((HERE / "rubric.json").read_text(encoding="utf-8"))["body"]

#: Answers that did not parse, across every call in this process (FR-008.48).
PARSE_FAILURES = 0

_VERDICT_LINE = re.compile(r"^VERDICT:\s*(?P<value>.+?)\s*$")


class RewardJudgeError(RuntimeError):
    """The judge could not give a reward after every attempt. Never a silent zero."""


class _ParseFailure(ValueError):
    pass


def _text(value: Any) -> str:
    """A plain or conversational prompt/completion as text (the last message's content)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and isinstance(value[-1], dict):
        return str(value[-1].get("content", ""))
    if isinstance(value, dict):
        return str(value.get("content", ""))
    return "" if value is None else str(value)


def parse(answer: str) -> str:
    """The verdict, by the rubric's parser (``verdict_line_v1`` or ``json_v1``)."""
    allowed = list(RUBRIC["allowed_verdicts"])
    if RUBRIC["parser"] == "verdict_line_v1":
        lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
        match = _VERDICT_LINE.match(lines[-1]) if lines else None
        if match is None or match.group("value") not in allowed:
            raise _ParseFailure("no allowed 'VERDICT: <value>' on the last line")
        return match.group("value")
    try:
        value = json.loads(answer)
    except (TypeError, ValueError):
        raise _ParseFailure("the answer is not JSON") from None
    verdict = value.get("verdict") if isinstance(value, dict) else None
    if not isinstance(verdict, str) or verdict not in allowed:
        raise _ParseFailure("no allowed verdict in the JSON answer")
    return verdict


def render(fields: dict[str, Any]) -> list[dict[str, str]]:
    values = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    values.setdefault("question", CONFIG.get("question") or "")
    messages = []
    for message in RUBRIC["messages"]:
        try:
            content = str(message["content"]).format_map(values)
        except (KeyError, IndexError, ValueError) as exc:
            raise RewardJudgeError(f"the rubric could not be rendered: {exc}") from None
        messages.append({"role": message["role"], "content": content})
    return messages


def _endpoint() -> tuple[str, str | None]:
    base = os.environ.get(CONFIG["base_url_env"], "").rstrip("/")
    if not base:
        raise RewardJudgeError(
            f"Set {CONFIG['base_url_env']} to the judge's OpenAI-compatible base URL (…/v1)."
        )
    return base, os.environ.get(CONFIG["api_key_env"]) or None


def _ask(messages: list[dict[str, str]], seed: int) -> str:
    base, key = _endpoint()
    body = {
        "model": CONFIG["judge_model_id"],
        "messages": messages,
        **CONFIG["sampling"],
        "seed": seed,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # noqa: S310 - the operator's own judge URL
        f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers
    )
    with urllib.request.urlopen(request, timeout=CONFIG["timeout_s"]) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"] or "")


def _retry_after(exc: urllib.error.HTTPError) -> float:
    try:
        return max(0.0, float(exc.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return 0.0


def score_one(fields: dict[str, Any], seed: int) -> float:
    """One completion's reward, or :class:`RewardJudgeError` after ``max_attempts``."""
    global PARSE_FAILURES
    messages = render(fields)
    last = "no attempt"
    for attempt in range(1, int(CONFIG["max_attempts"]) + 1):
        try:
            verdict = parse(_ask(messages, seed))
            return float(CONFIG["verdict_scores"][verdict])
        except _ParseFailure as exc:
            PARSE_FAILURES += 1
            last = f"parse failure: {exc}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            wait = _retry_after(exc) if exc.code in (429, 503) else 0.0
            time.sleep(wait or float(CONFIG["backoff_s"]) * attempt)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(float(CONFIG["backoff_s"]) * attempt)
    raise RewardJudgeError(
        f"The judge gave no usable answer after {CONFIG['max_attempts']} attempts ({last})."
    )


def judge_reward(prompts: list[Any], completions: list[Any], **kwargs: Any) -> list[float]:
    """TRL's reward-function signature: ``prompts``, ``completions``, ``completion_ids`` and every
    other dataset column as keyword lists. Returns one float per completion."""
    out: list[float] = []
    columns = {
        k: v for k, v in kwargs.items() if isinstance(v, list) and len(v) == len(completions)
    }
    for i, completion in enumerate(completions):
        fields = {k: _text(v[i]) for k, v in columns.items()}
        fields["prompt"] = _text(prompts[i]) if i < len(prompts) else ""
        fields["completion"] = _text(completion)
        out.append(score_one(fields, int(CONFIG["seed"]) + i))
    return out

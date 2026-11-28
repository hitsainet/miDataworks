"""Write an SSN pack's ``lookalike`` decoy list: numbers shaped like an SSN and not one.

ENH-006 D128 (operator, 2026-11-06). The list held 20 fixed values, reused across about 6,600 rows of
version 47, so a probe could learn the 20 values instead of the difference. This draws up to
``GenerateList``'s limit (1,000) from seeded families of everyday numbers.

Every value is admitted only when:
- no identifier reader of the live registry reads any part of it valid (``public_values.readings``):
  the pack is mirrored publicly (D38-2), so a constructed value must be invalid for every reader;
- a value with exactly nine digits is an SSN that cannot be issued (area 000, 666 or 900-999, group
  00, serial 0000): a nine-digit value in SSN context is a disclosure under D124 whatever its
  delimiters, so only an impossible one may be a look-alike;
- the pack's own rules do not fire ``ssn_hit`` on it in a sentence (the decoy's ``must_not_fire``).

Run from ``backend/``: ``python -m scripts.make_ssn_lookalikes --pack ../behaviours/<key>`` writes
the list into that pack's ``rule_set.json``; ``--check`` exits 1 when the file differs from what this
draws. The pack's key is data, so it is an argument (ADR-030: no behaviour is named in code).
``backend/tests/unit/test_ssn_lookalikes.py`` holds the pack's file to :func:`draw`.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections.abc import Callable
from pathlib import Path

from src.rules import public_values
from src.rules.compose import CompiledRuleSet
from src.rules.rule_set import RuleSetDocument
from src.rules.validators import resolve

PACKS = Path(__file__).resolve().parents[2] / "behaviours"
SEED = 128
LIMIT = 1000

DESCRIPTION = (
    "Numbers shaped like the element and not it, drawn by backend/scripts/make_ssn_lookalikes.py "
    "(seed 128, ENH-006 D128): telephone numbers in the fictional 555-01xx block, dates, ZIP+4 "
    "codes in the 9xxxx range, order, invoice, tracking and account references, lock "
    "combinations, times, network addresses and version numbers, amounts, and product codes with "
    "letters. Every value is invalid for every identifier reader, and one with exactly nine "
    "digits is an SSN that cannot be issued. None may fire ssn_hit."
)

_SSN = resolve("ssn_us", 1)
_DIGITS = re.compile(r"\D")


def _phone(r: random.Random) -> str:
    area = r.randint(201, 989)
    line = f"01{r.randint(0, 99):02d}"
    return r.choice(
        [
            f"({area}) 555-{line}",
            f"{area}-555-{line}",
            f"+1 {area} 555 {line}",
            f"{area}.555.{line}",
            f"555-{line}",
            f"1-{area}-555-{line}",
            f"{area} 555 {line}",
        ]
    )


_MONTHS = "January February March April May June July August September October November December"


def _date(r: random.Random) -> str:
    y, m, d = r.randint(1938, 2031), r.randint(1, 12), r.randint(1, 28)
    month = _MONTHS.split()[m - 1]
    return r.choice(
        [
            f"{y}-{m:02d}-{d:02d}",
            f"{m:02d}/{d:02d}/{y}",
            f"{d:02d}.{m:02d}.{y}",
            f"{month} {d}, {y}",
            f"{d} {month[:3]} {y}",
            f"{m}/{d}/{y % 100:02d}",
            f"{y}{m:02d}{d:02d}",
        ]
    )


def _zip4(r: random.Random) -> str:
    return f"9{r.randint(0, 9999):04d}-{r.randint(0, 9999):04d}"


def _reference(r: random.Random) -> str:
    n = r.randint
    return r.choice(
        [
            f"ORD-{n(2019, 2031)}-{n(100000, 999999)}",
            f"INV-{n(0, 9999):04d}-{n(0, 9999):04d}",
            f"#{r.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{n(1, 9)}"
            f"{r.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}-{n(10000, 99999)}",
            f"PO {n(1000, 9999)}-{n(10, 99)}-{r.choice('ABCDEFGH')}",
            f"{n(100, 999)}-{n(1000000, 9999999)}-{n(1000000, 9999999)}",
            f"Acct {n(1000, 9999)}-{n(100, 999)}",
            f"Case {n(10, 99)}-{n(10000, 99999)}",
            f"REF{n(10000000, 99999999)}",
            f"Ticket #{n(100000, 9999999)}",
        ]
    )


def _combination(r: random.Random) -> str:
    a, b, c = (r.randint(0, 59) for _ in range(3))
    return r.choice([f"{a:02d}-{b:02d}-{c:02d}", f"{a}-{b}-{c}", f"{a} {b} {c}"])


def _time(r: random.Random) -> str:
    h, m, s = r.randint(0, 23), r.randint(0, 59), r.randint(0, 59)
    return r.choice([f"{h:02d}:{m:02d}:{s:02d}", f"{h:02d}:{m:02d}", f"{h}h {m}m {s}s"])


def _address(r: random.Random) -> str:
    n = r.randint
    return r.choice(
        [
            f"192.168.{n(0, 255)}.{n(1, 254)}",
            f"10.{n(0, 255)}.{n(0, 255)}.{n(1, 254)}",
            f"172.{n(16, 31)}.{n(0, 255)}.{n(1, 254)}",
            f"v{n(1, 12)}.{n(0, 40)}.{n(0, 99)}",
            f"{n(1, 9)}.{n(0, 99)}.{n(0, 999)}",
        ]
    )


def _amount(r: random.Random) -> str:
    n = r.randint
    return r.choice(
        [
            f"${n(1, 999)},{n(0, 999):03d}.{n(0, 99):02d}",
            f"${n(1, 9999)}.{n(0, 99):02d}",
            f"{n(1, 99)},{n(0, 999):03d} units",
            f"{n(1, 999)}.{n(0, 9)} kg",
            f"EUR {n(1, 99)}.{n(0, 999):03d},{n(0, 99):02d}",
        ]
    )


def _product(r: random.Random) -> str:
    n = r.randint
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return r.choice(
        [
            f"SKU{n(10000, 99999)}{r.choice(letters)}",
            f"SN-{r.choice(letters)}{n(1, 9)}{r.choice(letters)}{r.choice(letters)}-{n(10000, 99999)}",
            f"{r.choice(letters)}{n(1, 9)}-{n(10000, 99999)}-{r.choice(letters)}",
            f"Model {r.choice(letters)}{r.choice(letters)}-{n(100, 9999)}",
            f"Part {n(100, 999)}-{n(1000, 9999)}{r.choice(letters)}",
        ]
    )


#: (family, share of the list, drawer). Shares sum to 1.
FAMILIES: list[tuple[str, float, Callable[[random.Random], str]]] = [
    ("phone", 0.18, _phone),
    ("date", 0.15, _date),
    ("zip4", 0.10, _zip4),
    ("reference", 0.18, _reference),
    ("combination", 0.06, _combination),
    ("time", 0.05, _time),
    ("address", 0.08, _address),
    ("amount", 0.08, _amount),
    ("product", 0.12, _product),
]


def admissible(value: str, compiled: CompiledRuleSet) -> bool:
    """The three conditions in the module docstring."""
    if any(True for _ in public_values.readings(value)):
        return False
    digits = _DIGITS.sub("", value)
    if len(digits) == 9 and _SSN.check(digits, "", "", {}).valid:
        return False
    fired = compiled.evaluate(f"value {value} here", None).outputs
    return not fired.get("ssn_hit")


def draw(rule_set: dict) -> list[str]:
    """The list, deterministic given the seed, the families and the live validators."""
    compiled = CompiledRuleSet(RuleSetDocument.model_validate(rule_set))
    rng = random.Random(SEED)  # noqa: S311 - a seeded draw of decoys, not a secret
    out: list[str] = []
    seen: set[str] = set()
    for _name, share, drawer in FAMILIES:
        want = round(LIMIT * share)
        got = 0
        tries = 0
        while got < want:
            tries += 1
            if tries > want * 200:
                raise RuntimeError(f"family {_name} could not reach {want} admissible values")
            value = drawer(rng)
            if value in seen or not admissible(value, compiled):
                continue
            seen.add(value)
            out.append(value)
            got += 1
    return out[:LIMIT]


def _with_list(rule_set: dict, values: list[str]) -> dict:
    decoys = [d for d in rule_set["decoys"] if d["id"] == "lookalike"]
    if len(decoys) != 1:
        raise RuntimeError("the rule set declares no single 'lookalike' decoy")
    decoys[0]["generator"] = {"list": values}
    decoys[0]["description"] = DESCRIPTION
    return rule_set


def main(argv: list[str]) -> int:
    if "--pack" not in argv or argv.index("--pack") + 1 >= len(argv):
        print("usage: python -m scripts.make_ssn_lookalikes --pack DIR [--check]")
        return 2
    rule_set = Path(argv[argv.index("--pack") + 1]) / "rule_set.json"
    raw = json.loads(rule_set.read_text(encoding="utf-8"))
    wanted = _with_list(json.loads(json.dumps(raw)), draw(raw))
    if "--check" in argv:
        return 0 if wanted == raw else 1
    rule_set.write_text(json.dumps(wanted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(wanted['decoys'][0]['generator']['list'])} lookalikes to {rule_set}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

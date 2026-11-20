"""Whether a cue's numbers count down or up (ADR 0040 section 3, plan section 2). Pure: no I/O.

Numbers are digits or English, Swedish or Danish words up to 32. Numbers joined by spaces or
punctuation ("3, 2, 1", "Zone 4. 3-2-1", "tre två ett") form a run. ``run_kind`` reads a run as:

- ``countdown`` when any three consecutive numbers in it step down by one ("Sprint 30 3 2 1 go"),
  or any two when a call follows the run ("go", "up", "kör", "op", "!": "2, 1, go!", "to, en,
  kør");
- ``beat_or_bar`` when any three consecutive numbers in it step up by one ("1-2-3-4"), or any
  two before a call ("1, 2, go").

Only two numbers of a run are ever left out, and only when no call follows the run:

- the first number, when a label word directly precedes it ("Zone 3 2 1 recovery" is zone 3
  then "2 1"; "Zones 5 4 3", "Gears 4, 3, 2");
- the last number, when minutes directly follow it ("Last 3 2 1 minutes").

The rest of the run is still read, so "Zone 4. 3, 2, 1, go!", "Gear 3: 1, 2, 3, 4" and
"Zones 5 4 3 2" are refused. When in doubt the rule refuses: the correction round recovers a
refused section-level cue, never a missed count.
"""

import itertools
import re
from typing import Final, Literal

RunKind = Literal["countdown", "beat_or_bar"]
RUN_LENGTH: Final = 3
CALLED_RUN_LENGTH: Final = 2  # a run a call follows ("2, 1, go!")

WORDS: Final[dict[str, int]] = {
    w: n
    for words, n in (
        ("one ett et", 1),
        ("two två to", 2),  # Danish "to"
        ("three tre", 3),
        ("four fyra fire", 4),
        ("five fem", 5),
        ("six sex seks", 6),
        ("seven sju syv", 7),
        ("eight åtta otte", 8),
        ("nine nio ni", 9),
        ("ten tio ti", 10),
        ("eleven elva elleve", 11),
        ("twelve tolv", 12),
        ("thirteen tretton tretten", 13),
        ("fourteen fjorton fjorten", 14),
        ("fifteen femton femten", 15),
        ("sixteen sexton seksten", 16),
        ("seventeen sjutton sytten", 17),
        ("eighteen arton atten", 18),
        ("nineteen nitton nitten", 19),
        ("twenty tjugo tyve", 20),
        ("twenty-four tjugofyra fireogtyve", 24),
        ("thirty trettio tredive", 30),
        ("thirty-two trettiotvå toogtredive", 32),
    )
    for w in words.split()
}
NUM: Final = r"(?:\d{1,3}|" + "|".join(sorted(map(re.escape, WORDS), key=len, reverse=True)) + ")"
_SEP: Final = r"(?:\s*[,.\-\u2013\u2014\u2026!:]+\s*|\s+)"
# In a run, Danish "en" is one too ("tre, to, en"); not in ``NUM``, where "takt" + "en" is a word.
_RUN_NUM: Final = rf"(?:{NUM}|en)"
_TOKEN: Final = re.compile(rf"\b{_RUN_NUM}\b")
_RUN: Final = re.compile(rf"\b{_RUN_NUM}(?:{_SEP}{_RUN_NUM})+\b")
_LABEL_BEFORE: Final = re.compile(
    r"\b(?:zones?|zon|zoner|zonerna|zonen|zonerne|gears?|växel|växlar|växlarna|gearene?|"
    r"levels?|nivå|nivåer|niveau|niveauer|steps?|steg|trin|rounds?|intervals?|sets?|reps?|"
    r"omgång|omgångar|varv|runde|runder|intervaller)\W*$"
)
_MINUTES_AFTER: Final = re.compile(r"^\W*(?:minutes?|mins?|minuter|minutter|min)\b")
_CALL_AFTER: Final = re.compile(
    r"^\s*!|^\W*(?:(?:and|then|och|sen|så|og)\s+)?"
    r"(?:go|up|now|stand|sprint|push|kör|upp|nu|stå|spurta|ös|kør|op|afsted|spurt)\b"
)


def value(token: str) -> int:
    """A number token's value: digits, or a word of ``WORDS``."""
    return int(token) if token.isdigit() else WORDS.get(token, 1)  # "en"


def _counted(text: str, run: re.Match[str]) -> tuple[list[int], int]:
    """The run's values without a label's number or a duration in minutes, and how many
    consecutive values make a count (see the module)."""
    values = [value(t) for t in _TOKEN.findall(run.group())]
    if _CALL_AFTER.search(text[run.end() :]):
        return values, CALLED_RUN_LENGTH
    start = 1 if _LABEL_BEFORE.search(text[: run.start()]) else 0
    end = len(values) - 1 if _MINUTES_AFTER.search(text[run.end() :]) else len(values)
    return values[start:end], RUN_LENGTH


def _longest(values: list[int], step: int) -> int:
    """The most consecutive values in ``values`` that each step by ``step`` (1 for one value)."""
    best = here = 1
    for a, b in itertools.pairwise(values):
        here = here + 1 if b - a == step else 1
        best = max(best, here)
    return best


def run_kind(text: str) -> RunKind | None:
    """A countdown or a count up in casefolded ``text``, as the module describes, or ``None``.
    When a run holds both, the longer one names it, a countdown on a tie."""
    down = up = 0
    for run in _RUN.finditer(text):
        values, length = _counted(text, run)
        if (n := _longest(values, -1)) >= length:
            down = max(down, n)
        if (n := _longest(values, 1)) >= length:
            up = max(up, n)
    if down or up:
        return "countdown" if down >= up else "beat_or_bar"
    return None

"""Which numbers in a cue are bound to a section-level use (ADR 0040 section 3, plan section 2).
Pure: no I/O.

On a track whose timing is not exact, a cue may hold a number only where it is bound (an
allowlist; ``unbound`` returns the others, which the validator refuses):

- **a label's number**: the one number directly after a zone, gear, level, round, interval,
  set, step or rep word, or after rpm, bpm, cadence or RPE, also with "at", "på" or the like
  after a rate ("Zone 4", "växel 3", "runde 2", "kadencen på 90");
- **minutes**: a number directly followed by minutes, unless "left", "more", "kvar",
  "tilbage" or the like follows ("2 minutes", "1,5 minuter"), or a clock length of at least a
  minute ("1:30");
- **a section-level unit after it**: rpm, bpm, %, percent, watts, "x" ("4 x"), gears ("two
  gears up"), an ordinal ending ("1st", "2:a");
- **a range or a set**: "Zone 3-4", "90-100 rpm", "2 to 3 minutes" (ascending, the other end
  bound), "2 of 4";
- **a year** (1900 to 2099).

Three or more numbers of the same use stepping by one either way are a count and are refused
("Zone 3, zone 2, zone 1", "3x, 2x, 1x", "1st, 2nd, 3rd"). A number next to a bar, beat or
count word never gets here (``timing_cue_unit_policy`` refuses it first).

Seconds, bars, beats, counts, a bare number and every other number are refused. A word that
is not always a number (``AMBIGUOUS``: "one", "en", "ett", "et", "to", "ni", "fire") is read as
one only where a number is plausible:

- next to another number ("tre, to"; two ambiguous words only across punctuation or "and", or
  when both are Danish: "to, en", "to en"), or in "N of M";
- before a unit ("en sekund", "to minutter") or after a label ("Niveau to");
- before "more", "left", "kvar", "mere" and the like that end the phrase ("one more!");
- alone before a call or the end, or there after "on", "på", "om", "til", "count", "tæl",
  "hold", "last", "sista", "the", "and", "och", "og" ("On one!", "På ett, kör", "Last one!", "And
  one!").

Otherwise it is the article or word it also is ("en sista klättring", "one more climb", "back
to zone 3", "legs on fire", "fire up").
"""

import re
from typing import Final

from classbeat.agent.timing_cue_number_model import NumberToken, number_tokens

_RATE: Final = r"(?:rpm|bpm|cadence|kadens|kadensen|kadence|kadencen)"
_LABEL: Final = (
    r"(?:zones?|zon|zonen|zoner|zonerna|zonerne|gears?|gearet|gearene|växel|växeln|växlar|"
    r"levels?|nivå|nivån|nivåer|niveau|niveauet|niveauer|rounds?|omgång|omgången|omgångar|"
    r"runde|runden|runder|intervals?|intervall|intervallet|intervaller|intervallerne|sets?|"
    rf"sæt|sættet|steps?|steg|steget|trin|trinnet|reps?|rpe|{_RATE})"
)
_LABEL_BEFORE: Final = re.compile(
    rf"\b{_LABEL}\s*[:#]?\s*$|\b{_RATE}\s+(?:at|of|around|på|i|kring|omkring|til|ved|om)\s+$"
)
_MINUTES: Final = r"(?:minutes?|mins?|minut|minuten|minuter|minuterna|minutter|minutterne|minuttet)"
# Words after a length that make it a countdown ("2 minutes left", "1:00 to go").
_COUNTING: Final = r"(?!\W*(?:left|remaining|to\s+go|more|kvar|till|mer|tilbage|igen|mere)\b)"
_MINUTES_AFTER: Final = re.compile(rf"^\s*-?\s*{_MINUTES}\b{_COUNTING}")
_CLOCK_AFTER: Final = re.compile(rf"^{_COUNTING}")
_UNIT_AFTER: Final = re.compile(
    r"^(?:\s*(?:%|percent\b|procent\b|pct\b|rpm\b|bpm\b|watts?\b|w\b|x\b|\u00d7|gears?\b|"
    r"växel\b|växlar\b|gear\b)|(?:st|nd|rd|th|:a|:e)\b)"
)
_RANGE: Final = re.compile(r"^\s*(?:-|\u2013|\u2014|to|till|til|or|eller)\s*$")
_OF: Final = re.compile(r"^\s+(?:of|av|af)\s+$")
_GAP: Final = re.compile(
    r"^(?:[\s,.\-\u2013\u2014\u2026!:;/|]|\band\b|\boch\b|\bog\b|\bthen\b|\bsen\b)+$"
)
_UNIT: Final = (
    r"(?:s|secs?|seconds?|sek|sekund\w*|bars?|beats?|counts?|takt\w*|slag\w*|tælling\w*|"
    + _MINUTES
    + ")"
)
_DANISH: Final = frozenset({"en", "et", "to", "ni", "fire"})
_CLAUSE_START: Final = re.compile(r"(?:^|[!,.:;?\u2026-])\s*$")
_EN: Final = ("on|at|in|count|hold|last|final|and|the", "go|up|now|stand|sprint|push")
_SV: Final = ("på|om|till|räkna|håll|sista|och", "kör|upp|nu|stå|spurta|gå")
_DA: Final = ("på|om|til|tæl|tælle|hold|sidste|og", "kør|op|nu|afsted|spurt|start")
# The prepositions and calls of the language each ambiguous word is a number in.
_CONTEXT: Final[dict[str, tuple[str, str]]] = {
    "one": _EN,
    "ett": _SV,
    "en": (f"{_SV[0]}|{_DA[0]}", f"{_SV[1]}|{_DA[1]}"),
}
_MORE: Final = (
    r"(?:more|mer|mere|till|til|times|gånger\s+till|gange\s+til|to\s+go|left|kvar|tilbage)"
)


def _plausible(text: str, t: NumberToken, tokens: list[NumberToken]) -> bool:
    """Whether an ambiguous word ``t`` reads as a number here (see the module)."""
    before, after = text[: t.start], text[t.end :]
    if re.match(rf"^\s*-?\s*{_UNIT}\b", after) or _LABEL_BEFORE.search(before):
        return True
    word = text[t.start : t.end]
    for other in tokens:
        if other is t:
            continue
        gap = text[other.end : t.start] if other.end <= t.start else text[t.end : other.start]
        if _OF.match(gap):
            return True
        # "to" is English too: a number only next to a number word ("tre, to"), not "to 100".
        if not _GAP.match(gap) or (word == "to" and not other.word):
            continue
        # Two ambiguous words count only with a mark between ("to, en"), or both Danish.
        other_word = text[other.start : other.end]
        if not other.ambiguous or gap.strip() or {word, other_word} <= _DANISH:
            return True
    preps, calls = _CONTEXT.get(word, _DA)
    ends = rf"\s*(?:$|[!,.:;?\u2026-]|(?:(?:and|och|og)\s+)?(?:{calls})\b)"
    if re.match(rf"^\s*{_MORE}{ends}", after):  # "one more!", "fire mere, spurt"
        return True
    if not re.match(rf"^{ends}", after):
        return False
    return bool(_CLAUSE_START.search(before) or re.search(rf"\b(?:{preps})\s+$", before))


def _bound_alone(text: str, t: NumberToken) -> bool:
    before, after = text[: t.start], text[t.end :]
    if t.clock:
        return t.value >= 1 and bool(_CLOCK_AFTER.match(after))
    year = not t.word and t.value.is_integer() and 1900 <= t.value <= 2099
    return bool(
        year
        or _LABEL_BEFORE.search(before)
        or _MINUTES_AFTER.match(after)
        or _UNIT_AFTER.match(after)
    )


def unbound(text: str) -> list[str]:
    """The numbers of casefolded ``text`` that no section-level use binds, in order."""
    found = number_tokens(text)
    tokens = [t for t in found if not t.ambiguous or _plausible(text, t, found)]
    bound = [_bound_alone(text, t) for t in tokens]
    paired: set[int] = set()  # a number is one end of one range or set, never a chain of them
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        gap = text[a.end : b.start]
        if i in paired:
            continue
        if _OF.match(gap) or (
            _RANGE.match(gap) and b.value > a.value and (bound[i] or bound[i + 1])
        ):
            bound[i] = bound[i + 1] = True
            paired |= {i, i + 1}
    for i in _counted_uses(text, tokens):
        bound[i] = False
    return [text[t.start : t.end] for t, ok in zip(tokens, bound, strict=True) if not ok]


def _use(text: str, t: NumberToken) -> str | None:
    """What binds ``t`` alone: its label word or the unit after it, or ``None``."""
    before, after = text[: t.start], text[t.end :]
    if m := _LABEL_BEFORE.search(before):
        return m.group().split()[0].rstrip("s:#")
    if m := _MINUTES_AFTER.match(after) or _UNIT_AFTER.match(after):
        unit = m.group().strip()
        return "ordinal" if unit in {"st", "nd", "rd", "th", ":a", ":e"} else unit[:3]
    return None


def _counted_uses(text: str, tokens: list[NumberToken]) -> set[int]:
    """Three or more numbers of the same use stepping by one either way: a count ("Zone 3,
    zone 2, zone 1", "Rep 1, rep 2, rep 3", "3x, 2x, 1x", "1st, 2nd, 3rd")."""
    uses = [(i, u) for i, t in enumerate(tokens) if (u := _use(text, t)) is not None]
    out: set[int] = set()
    for (i, a), (j, b), (k, c) in zip(uses, uses[1:], uses[2:], strict=False):
        x, y, z = tokens[i].value, tokens[j].value, tokens[k].value
        if a == b == c and y - x == z - y and abs(y - x) == 1:
            out |= {i, j, k}
    return out

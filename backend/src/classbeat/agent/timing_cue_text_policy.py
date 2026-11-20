"""Whether a cue's text gives a beat-, bar- or drop-precise instruction (ADR 0040 section 3: "the
design validator refuses any beat- or bar-precise instruction on a track that is not exact. That
covers cue times, drop hits and countdowns"). Pure: no I/O.

``precise_kind`` reads English, Swedish and Danish (the languages classbeat writes in), with
numbers as digits or words up to 32:

- ``countdown``: "count down", a run of three or more numbers each one less than the one before
  ("3, 2, 1", "tre två ett"), and seconds as a countdown: "<n> seconds to go / left / kvar /
  tilbage", "last / final / sista / sidste <n> s", "in / om / within <n> s", or a bare short call
  of at most ``SHORT_CALL_S`` seconds ("ten seconds"; a longer bare one, "30 s hard", is an
  interval's length);
- ``beat_or_bar``: "<n> bars / beats / counts / takter / slag", "bar <n>", "next / last bar",
  "downbeat", "beat one", "on the one", "count 1 2 3 4" and a run of numbers counting up
  ("1-2-3-4");
- ``drop_hit``: the drop as a noun ("the drop", "a drop", "droppen", "droppet") after any
  word, "drop hits / coming", "when it drops", "när det droppar", "når det dropper".

A number run that follows a zone, gear or level word ("Zone 3 2 1 recovery", "Gears 4, 3, 2")
or is followed by minutes ("3 2 1 minutes") is a section-level cue, not a count. "Ride on the
beat" is a cadence from the BPM, which an approximate track keeps, and is allowed; so is the
verb "drop" ("drop to zone 2"). It is a pattern list over free text: the second line, after the
model being told each track's class (``timing_input``).
"""

import itertools
import re
from typing import Final, Literal

PreciseKind = Literal["countdown", "beat_or_bar", "drop_hit"]
SHORT_CALL_S: Final = 10

_WORDS: Final[dict[str, int]] = {
    w: n
    for words, n in (
        ("one ett et", 1),
        ("two två to", 2),  # Danish "to"; never a bare "in"/"om" call (English "in to")
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
_NUM: Final = r"(?:\d{1,3}|" + "|".join(sorted(map(re.escape, _WORDS), key=len, reverse=True)) + ")"
_SEP: Final = r"(?:\s*[,.\-\u2013\u2014\u2026!:]+\s*|\s+)"
_RUN: Final = re.compile(rf"\b{_NUM}(?:{_SEP}{_NUM}){{2,}}\b")
_NOT_A_COUNT_BEFORE: Final = re.compile(
    r"\b(?:zones?|zon|zoner|zonerna|zonen|zoner|zonerne|gears?|växel|växlar|växlarna|gearene?|"
    r"levels?|nivå|nivåer|niveau|niveauer|steps?|steg|trin|rounds?|intervals?|sets?|reps?|"
    r"omgång|omgångar|varv|runde|runder|intervaller)\W*$"
)
_NOT_A_COUNT_AFTER: Final = re.compile(r"^\W*(?:minutes?|mins?|minuter|minutter|min)\b")
_SEC: Final = r"(?:s|secs?|seconds?|sek|sekund|sekunder|sekunden|sekunderna|sekundet|sekunderne)"
_UNIT: Final = (
    r"(?:bars?|beats?|counts?|takt|takter|takterna|takten|taktslag|slag|slagen|slaget|"
    r"tællinger|tælling)"
)
# A bare "in"/"om" call ("go in 3", "om tre"): not "one"/"ett"/"et"/"to" ("om ett", "in to"),
_CALL_NUM: Final = r"(?!(?:one|ett|et|to)\b)" + _NUM
# ... and only where the call ends the phrase ("go in 3!", "kör om fem"), not "in 2 sets".
_CALL_END: Final = (
    r"(?=\s*(?:$|[!,.:;\u2026-]|(?:go|up|stand|sprint|push|now|and|kör|upp|nu|och|op|og)\b))"
)
_PATTERNS: Final[tuple[tuple[PreciseKind, re.Pattern[str]], ...]] = (
    (
        "countdown",
        re.compile(
            r"\bcount(?:ing)?[\s-]*(?:it\s+)?down\b|\bcountdown\b|\bt[\s-]*minus\b"
            r"|\bnedräkning\w*|\bräkna\s+(?:ner|ned)\b"
            r"|\bnedtælling\w*|\btæl(?:le)?\s+ned\b|\btre\W+to\W+(?:en|et)\b"
            rf"|\b{_NUM}\s*{_SEC}\s+(?:to\s+go|left|remaining|kvar|tilbage|igen)\b"
            rf"|\b{_SEC}\s+(?:left|remaining|to\s+go|kvar|tilbage)\W*{_NUM}\b"
            rf"|\b(?:last|final|sista|sidste)\s+(?:{_NUM}\s*)?{_SEC}\b"
            rf"|\b(?:last|final|sista|sidste)\s+{_NUM}\s*(?:$|[!,.:;])"
            rf"|\b(?:in|within|om|inom|inden\s+for|på)\s+{_NUM}\s*{_SEC}\b"
            rf"|\b(?:in|om)\s+{_CALL_NUM}\b{_CALL_END}"
        ),
    ),
    (
        "beat_or_bar",
        re.compile(
            rf"\b{_NUM}[\s-]*{_UNIT}\b|\b(?:bar|takt|takten)\s*{_NUM}\b"
            r"|\b(?:next|this|last|final|first)\s+(?:bar|beat)\b"
            r"|\b(?:nästa|sista|första|næste|sidste|første)\s+(?:takt|takten|slag|slaget)\b"
            r"|\b(?:every|each|var|varje|hver)\s+(?:\w+\s+)?(?:bar|takt)\b"
            r"|\b(?:start|beginning|end)\s+of\s+the\s+bar\b"
            r"|\b(?:change|switch|shift|byt|byte|skifta|skift|skifte)\s+(?:on|at|på|ved)\s+"
            r"(?:the\s+)?(?:bar|beat|takten|slaget)\b"
            r"|\bdown[\s-]*beats?\b|\bbeat\s+(?:one|1)\b|\bon\s+the\s+(?:one|1)\b"
            r"|\bpå\s+(?:ettan|etteren|et-tallet|1:an)\b"
            rf"|\b(?:count|räkna|tæl|tælle)\s+{_NUM}\b"
        ),
    ),
    (
        "drop_hit",
        re.compile(
            r"\b(?:the|a|this|next|that|its|big|first|last)\s+drops?\b"
            r"|\b(?:at|on|before|after|until|till|for|into)\s+drop\b|\b(?:post|pre)[\s-]drop\b"
            rf"|\bdrop(?:'s|s)?\s+(?:hits|lands|comes|coming|incoming|kicks|is|in\s+{_NUM}|om\s+{_NUM})"
            r"|\bwhen\s+(?:it|the\s+\w+)\s+drops\b|\b(?:beat|bass)\s+drops?\b"
            r"|\bdropp(?:en|et|ens|ets)\b|\b(?:ett|en|et)\s+drop\b"
            r"|\b(?:det|när\s+\w+|når\s+\w+)\s+(?:droppar|dropper)\b"
        ),
    ),
)


def _value(token: str) -> int:
    return int(token) if token.isdigit() else _WORDS[token]


def _run_kind(text: str) -> PreciseKind | None:
    """A run of three or more numbers counting down by one, or up by one, as a cue."""
    for run in _RUN.finditer(text):
        if _NOT_A_COUNT_BEFORE.search(text[: run.start()]) or _NOT_A_COUNT_AFTER.search(
            text[run.end() :]
        ):
            continue
        values = [_value(t) for t in re.findall(_NUM, run.group())]
        steps = {b - a for a, b in itertools.pairwise(values)}
        if steps == {-1}:
            return "countdown"
        if steps == {1}:
            return "beat_or_bar"
    return None


def _short_call(text: str) -> bool:
    """A bare call of at most ``SHORT_CALL_S`` seconds ("ten seconds"), a countdown."""
    return any(
        _value(m.group(1)) <= SHORT_CALL_S
        for m in re.finditer(rf"\b({_NUM})\s*{_SEC}\b", text)
        if not re.search(r"\bx\s*$", text[: m.start()])  # "2 x 30 s" is a set of lengths
    )


def precise_kind(text: str) -> PreciseKind | None:
    """The kind of beat-, bar- or drop-precise instruction ``text`` gives, or ``None``."""
    folded = text.casefold()
    if found := _run_kind(folded):
        return found
    if _short_call(folded):
        return "countdown"
    return next((kind for kind, pattern in _PATTERNS if pattern.search(folded)), None)

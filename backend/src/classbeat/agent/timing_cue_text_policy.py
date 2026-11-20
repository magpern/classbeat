"""Whether a cue's text gives a beat-, bar- or drop-precise instruction (ADR 0040 section 3: "the
design validator refuses any beat- or bar-precise instruction on a track that is not exact. That
covers cue times, drop hits and countdowns"). Pure: no I/O.

``precise_kind`` reads English, Swedish and Danish (the languages classbeat writes in), with
numbers as digits or words up to 32:

- ``countdown``: "count down", a run of numbers counting down (``timing_cue_run_policy``:
  "3, 2, 1", "tre två ett", "Zone 4: 3 2 1 go"), and seconds as a countdown: "<n> seconds to go
  / left / kvar / tilbage", "last / final / sista / sidste <n> s", "in / om / within <n> s", or
  a bare short call of at most ``SHORT_CALL_S`` seconds ("ten seconds"; a longer bare one,
  "30 s hard", is an interval's length);
- ``beat_or_bar``: "<n> bars / beats / counts / takter / slag", "bar <n>", "next / last bar",
  "downbeat", "beat one", "on the one", "count 1 2 3 4" and a run of numbers counting up
  ("1-2-3-4");
- ``drop_hit``: the drop as a noun ("the drop", "a drop", "droppen", "droppet") after any
  word, "drop hits / coming", "when it drops", "när det droppar", "når det dropper".

"Ride on the beat" is a cadence from the BPM, which an approximate track keeps, and is allowed;
so is the verb "drop" ("drop to zone 2"). It is a pattern list over free text: the second line,
after the model being told each track's class (``timing_input``).
"""

import re
from typing import Final, Literal

from classbeat.agent.timing_cue_run_policy import NUM, run_kind, value

PreciseKind = Literal["countdown", "beat_or_bar", "drop_hit"]
SHORT_CALL_S: Final = 10

_SEC: Final = r"(?:s|secs?|seconds?|sek|sekund|sekunder|sekunden|sekunderna|sekundet|sekunderne)"
_UNIT: Final = (
    r"(?:bars?|beats?|counts?|takt|takter|takterna|takten|taktslag|slag|slagen|slaget|"
    r"tællinger|tælling)"
)
# A bare "in"/"om" call ("go in 3", "om tre"): not "one"/"ett"/"et"/"to" ("om ett", "in to"),
_CALL_NUM: Final = r"(?!(?:one|ett|et|to)\b)" + NUM
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
            rf"|\b{NUM}\s*{_SEC}\s+(?:to\s+go|left|remaining|kvar|tilbage|igen)\b"
            rf"|\b{_SEC}\s+(?:left|remaining|to\s+go|kvar|tilbage)\W*{NUM}\b"
            rf"|\b(?:last|final|sista|sidste)\s+(?:{NUM}\s*)?{_SEC}\b"
            rf"|\b(?:last|final|sista|sidste)\s+{NUM}\s*(?:$|[!,.:;])"
            rf"|\b(?:in|within|om|inom|inden\s+for|på)\s+{NUM}\s*{_SEC}\b"
            rf"|\b(?:in|om)\s+{_CALL_NUM}\b{_CALL_END}"
        ),
    ),
    (
        "beat_or_bar",
        re.compile(
            rf"\b{NUM}[\s-]*{_UNIT}\b|\b(?:bar|takt|takten)\s*{NUM}\b"
            r"|\b(?:next|this|last|final|first)\s+(?:bar|beat)\b"
            r"|\b(?:nästa|sista|första|næste|sidste|første)\s+(?:takt|takten|slag|slaget)\b"
            r"|\b(?:every|each|var|varje|hver)\s+(?:\w+\s+)?(?:bar|takt)\b"
            r"|\b(?:start|beginning|end)\s+of\s+the\s+bar\b"
            r"|\b(?:change|switch|shift|byt|byte|skifta|skift|skifte)\s+(?:on|at|på|ved)\s+"
            r"(?:the\s+)?(?:bar|beat|takten|slaget)\b"
            r"|\bdown[\s-]*beats?\b|\bbeat\s+(?:one|1)\b|\bon\s+the\s+(?:one|1)\b"
            r"|\bpå\s+(?:ettan|etteren|et-tallet|1:an)\b"
            rf"|\b(?:count|räkna|tæl|tælle)\s+{NUM}\b"
        ),
    ),
    (
        "drop_hit",
        re.compile(
            r"\b(?:the|a|this|next|that|its|big|first|last)\s+drops?\b"
            r"|\b(?:at|on|before|after|until|till|for|into)\s+drop\b|\b(?:post|pre)[\s-]drop\b"
            rf"|\bdrop(?:'s|s)?\s+(?:hits|lands|comes|coming|incoming|kicks|is|in\s+{NUM}|om\s+{NUM})"
            r"|\bwhen\s+(?:it|the\s+\w+)\s+drops\b|\b(?:beat|bass)\s+drops?\b"
            r"|\bdropp(?:en|et|ens|ets)\b|\b(?:ett|en|et)\s+drop\b"
            r"|\b(?:det|när\s+\w+|når\s+\w+)\s+(?:droppar|dropper)\b"
        ),
    ),
)


def _short_call(text: str) -> bool:
    """A bare call of at most ``SHORT_CALL_S`` seconds ("ten seconds"), a countdown."""
    return any(
        value(m.group(1)) <= SHORT_CALL_S
        for m in re.finditer(rf"\b({NUM})\s*{_SEC}\b", text)
        if not re.search(r"\bx\s*$", text[: m.start()])  # "2 x 30 s" is a set of lengths
    )


def precise_kind(text: str) -> PreciseKind | None:
    """The kind of beat-, bar- or drop-precise instruction ``text`` gives, or ``None``."""
    folded = text.casefold()
    if found := run_kind(folded):
        return found
    if _short_call(folded):
        return "countdown"
    return next((kind for kind, pattern in _PATTERNS if pattern.search(folded)), None)

"""The countdown, beat and drop idioms a cue may not use on a track that is not exact, in
English, Swedish and Danish, where no number shows the count (ADR 0040 section 3). Pure: no I/O.

A number in a cue is checked by ``timing_cue_binding_policy``; this fixed list covers what has
no number: "count down", "ready, set, go" and a call after a ready or wait word ("Ready,
steady, go", "Wait... wait... now"), "on my count", "count with me", "on the beat / upbeat /
snare", "last seconds", "the next bar", "downbeat", "on the drop", "until it drops", "when the
bass kicks in", "drop's here" and their Swedish and Danish forms. A bare "Drop!" and the verb
("drop to zone 2", "drop a gear") are allowed.
"""

import re
from typing import Final, Literal

IdiomKind = Literal["countdown", "beat_or_bar", "drop_hit"]

_SECONDS: Final = r"(?:seconds|secs|sekunder|sekunderna|sekunden|sekundet|sekunderne)"
_IDIOMS: Final[tuple[tuple[IdiomKind, re.Pattern[str]], ...]] = (
    (
        "countdown",
        re.compile(
            r"\bcount(?:ing)?[\s-]*(?:it\s+)?(?:down|in|to|of)\b|\bcountdown\b|\bt[\s-]*minus\b"
            r"|\bthe\s+count\s+of\b|\b(?:on|at)\s+my\s+(?:count|call|mark|signal)\b"
            r"|\bnedräkning\w*|\bräkna\s+(?:ner|ned|till|in)\b|\bpå\s+min\s+(?:räkning|signal)\b"
            r"|\bnedtælling\w*|\btæl(?:le)?\s+(?:ned|til|ind)\b|\bpå\s+min\s+tælling\b"
            r"|\bpå\s+mit\s+signal\b|\bready\W+set\W+go\b|\bon\s+your\s+marks?\b"
            r"|\bklara\W+färdiga\W+gå\b|\bpå\s+era\s+platser\b"
            r"|\bklar\W+(?:parat|færdig)\W+(?:start|gå|kør)\b|\bpå\s+pladserne\b"
            rf"|\b{_SECONDS}\b"
            # a call after a "ready" or "wait" word: "Ready, steady, go", "Get set... go",
            # "Färdiga, gå", "Parat... start", "Wait... wait... NOW", "Ready and one, go"
            rf"|\b(?:ready|get\s+set|klara|färdiga|klar|parat|færdig|wait|vänta|vent)\W+"
            rf"(?:\w+\W+){{0,2}}?(?:go|now|gå|start|kör|kør|nu)\b"
            r"|\bwait\W+wait\b|\bvänta\W+vänta\b|\bvent\W+vent\b"
            r"|\bcount\s+with\s+me\b|\bräkna\s+med\s+mig\b|\btæl\s+med\s+mig\b"
        ),
    ),
    (
        "beat_or_bar",
        re.compile(
            r"\b(?:next|this|last|final|first)\s+(?:bar|beat)\b"
            r"|\b(?:nästa|sista|första|næste|sidste|første)\s+(?:takt|takten|slag|slaget)\b"
            r"|\b(?:every|each|var|varje|hver|varannan|vartannat)\s+(?:\w+\s+)?(?:bar|beat|takt|slag)\b"
            r"|\bon\s+the\s+and\b"
            r"|\b(?:start|beginning|end)\s+of\s+the\s+bar\b"
            r"|\b(?:change|switch|shift|byt|byte|skifta|skift|skifte)\s+(?:on|at|på|ved)\s+"
            r"(?:the\s+)?(?:bar|beat|takten|slaget)\b"
            r"|\bon\s+the\s+(?:(?:up|off|down)[\s-]?)?(?:beat|count|bar|one|1)\b"
            r"|\bon\s+the\s+(?:snare|kick|clap|hi-?hat)\b|\bpå\s+(?:takten|slaget|taktslaget)\b"
            r"|\bpå\s+(?:räkningen|tællingen|ettan|etteren|et-tallet|1:an)\b"
            r"|\bdown[\s-]*beats?\b|\bbeat\s+(?:one|1)\b"
            r"|\b(?:few|couple\s+of|några|par|nogle|få)\s+(?:bars|beats|takter|slag)\b"
        ),
    ),
    (
        "drop_hit",
        re.compile(
            r"\b(?:the|a|this|next|that|its|big|first|last)\s+drops?\b"
            r"|\b(?:at|on|before|after|until|till|for|into)\s+drop\b|\b(?:post|pre)[\s-]drop\b"
            r"|\bdrop(?:['\u2019]s|s)?\s+(?:hits?|lands|comes|coming|incoming|kicks|is|here)\b"
            r"|\b(?:bass|beat|drop|kick)\s+kicks?\s+in\b|\bit\s+drops\b|\bdropp\b"
            r"|\b(?:when|until|till|before|as|once|after)\s+(?:it|the\s+\w+|this|that)\s+drops\b"
            r"|\b(?:beat|bass)\s+drops?\b|\bwait\s+for\s+it\b|\bvänta\s+på\s+det\b"
            r"|\bvent\s+på\s+det\b|\bdropp(?:en|et|ens|ets)\b|\b(?:ett|en|et)\s+drop\b"
            r"|\b(?:det|när\s+\w+|når\s+\w+|tills\s+\w+|indtil\s+\w+)\s+(?:droppar|dropper)\b"
        ),
    ),
)


def idiom_kind(text: str) -> IdiomKind | None:
    """The idiom casefolded ``text`` uses, or ``None``."""
    return next((kind for kind, pattern in _IDIOMS if pattern.search(text)), None)

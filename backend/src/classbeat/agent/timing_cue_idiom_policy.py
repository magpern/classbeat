"""The countdown, beat and drop idioms a cue may not use on a track that is not exact, in
English, Swedish and Danish, where no number shows the count (ADR 0040 section 3). Pure: no I/O.

A number in a cue is checked by ``timing_cue_binding_policy``; this fixed list covers what has
no number: "count down", "ready, set, go", "on my count", "on the beat", "last seconds", "the
next bar", "downbeat", "on the drop", "until it drops" and their Swedish and Danish forms. A
bare "Drop!" and the verb ("drop to zone 2", "drop a gear") are allowed.
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
        ),
    ),
    (
        "beat_or_bar",
        re.compile(
            r"\b(?:next|this|last|final|first)\s+(?:bar|beat)\b"
            r"|\b(?:nästa|sista|första|næste|sidste|første)\s+(?:takt|takten|slag|slaget)\b"
            r"|\b(?:every|each|var|varje|hver)\s+(?:\w+\s+)?(?:bar|takt)\b"
            r"|\b(?:start|beginning|end)\s+of\s+the\s+bar\b"
            r"|\b(?:change|switch|shift|byt|byte|skifta|skift|skifte)\s+(?:on|at|på|ved)\s+"
            r"(?:the\s+)?(?:bar|beat|takten|slaget)\b"
            r"|\bon\s+the\s+(?:beat|count|bar|one|1)\b|\bpå\s+(?:takten|slaget|taktslaget)\b"
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
            r"|\bdrop(?:'s|s)?\s+(?:hits?|lands|comes|coming|incoming|kicks|is)\b"
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

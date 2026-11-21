"""A number next to a bar, beat or count word: the position or length of a bar, beat or count,
refused on a track that is not exact before any binding is read (ADR 0040 section 3). Pure: no
I/O.

``unit_count`` finds, in English, Swedish and Danish:

- a number, a digit ordinal ("8th", "8:e", "8."), an ordinal word ("third", "fjärde",
  "ottende"), an article or "half" ("a", "en", "ett", "et", "halv") **before** a bar, beat or
  count word, an article directly ("a bar", "en takt"), the others also with one word between
  ("one more bar", "the last 4 of 8 counts", "fyra korta takter");
- a number **after** one, directly ("bar 8", "beat 3 of 4", "takt ett", "bar one").

So "Raise the bar", "hands on the bars", "follow the beat", "make it count" and "håll en jämn
takt" pass. This runs first, so "N of M" and an ambiguous word bind nothing next to a bar.
"""

import re
from typing import Final

from classbeat.agent.timing_cue_number_model import WORD

_ORDINAL: Final = (
    r"(?:(?:\w+-)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)|tenth|"
    r"eleventh|twelfth|\w+teenth|\w+tieth|första|förste|andra|andre|tredje|fjärde|femte|"
    r"sjätte|sjunde|\w+onde|elfte|tolfte|første|anden|andet|fjerde|sjette|\w+ende|ellevte|"
    r"tolvte|tredivte)"
)
_DIGITS: Final = r"\d+(?:st|nd|rd|th|:a|:e|\.)?"
_NUMBER: Final = rf"(?:{_DIGITS}|{_ORDINAL}|(?:{WORD})(?:th|:a|:e)?)"  # also "fiveth", "nio:e"
_UNITS: Final = (
    r"(?:bars?|beats?|counts?|(?:down|up|off)[\s-]?beats?|takt|takter|takten|takterna|"
    r"taktslag\w*|slag|slagen|slaget|tælling\w*)"
)
_UNIT: Final = r"(?:bar|beat|count|takt|slag|tælling)"
_ARTICLE: Final = r"(?:an?|en|ett|et|half|halv|halva|halve)"
_PATTERN: Final = re.compile(
    rf"(?<!\w)(?:(?!(?:en|ett|et)\b){_NUMBER}(?:[\s-]+\w+)?|{_ARTICLE})[\s-]+{_UNITS}\b"
    rf"|\b{_UNIT}(?:[\s:#-]+(?:\d+|{WORD})|\d+)(?!\w)"
)


def unit_count(text: str) -> bool:
    """Whether casefolded ``text`` puts a number next to a bar, beat or count word."""
    return _PATTERN.search(text) is not None

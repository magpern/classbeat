"""The numbers in a cue: digits and English, Swedish and Danish number words from 0 to 100,
compounds included (``twenty-five``, ``tjugofem``, ``femogtyve``). Pure: no I/O.

``number_tokens`` finds every number in casefolded text. Digits are any Unicode decimal digits
(full-width too), with a decimal part ("1,5", "2.5") and a clock form ("1:30"). A word that is
also a common non-number word (``AMBIGUOUS``: English "one", Swedish "en"/"ett", Danish
"en"/"et"/"to"/"ni"/"fire") is marked, and ``timing_cue_binding_policy`` decides from its
context whether it is read as a number.
"""

import re
from dataclasses import dataclass
from typing import Final

_EN_UNITS: Final = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_EN_TEENS: Final = "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
_EN_TENS: Final = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SV_UNITS: Final = ["ett", "två", "tre", "fyra", "fem", "sex", "sju", "åtta", "nio"]
_SV_TEENS: Final = "tio elva tolv tretton fjorton femton sexton sjutton arton nitton"
_SV_TENS: Final = (
    ("tjugo", "tjugu", "tjuge"),
    ("trettio", "tretti"),
    ("fyrtio", "fyrti"),
    ("femtio", "femti"),
    ("sextio", "sexti"),
    ("sjuttio", "sjutti"),
    ("åttio", "åtti"),
    ("nittio", "nitti"),
)
_DA_UNITS: Final = ["en", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni"]
_DA_TEENS: Final = "ti elleve tolv tretten fjorten femten seksten sytten atten nitten"
_DA_TENS: Final = [
    "tyve",
    "tredive",
    "fyrre",
    "halvtreds",
    "tres",
    "halvfjerds",
    "firs",
    "halvfems",
]

AMBIGUOUS: Final = frozenset({"one", "en", "ett", "et", "to", "ni", "fire"})


def _words() -> dict[str, int]:
    """Every number word of the three languages and its value."""
    out = {"zero": 0, "noll": 0, "nul": 0, "et": 1, "hundred": 100, "hundra": 100}
    out |= {"hundrede": 100, "one hundred": 100, "a hundred": 100, "etthundra": 100}
    for units, teens in ((_EN_UNITS, _EN_TEENS), (_SV_UNITS, _SV_TEENS), (_DA_UNITS, _DA_TEENS)):
        out |= {w: n for n, w in enumerate(units, 1)}
        out |= {w: n for n, w in enumerate(teens.split(), 10)}
    out["en"] = 1  # Swedish too
    sv_units = [*_SV_UNITS, "en"]
    for tens, n in zip(_EN_TENS, range(20, 100, 10), strict=True):
        out[tens] = n
        for u, w in enumerate(_EN_UNITS, 1):
            out |= {f"{tens}-{w}": n + u, f"{tens} {w}": n + u, f"{tens}{w}": n + u}
    for forms, n in zip(_SV_TENS, range(20, 100, 10), strict=True):
        for tens in forms:
            out[tens] = n
            out |= {f"{tens}{w}": n + (sv_units.index(w) % 9) + 1 for w in sv_units}
    for tens, n in zip(_DA_TENS, range(20, 100, 10), strict=True):
        out[tens] = n
        out |= {f"{w}og{tens}": n + u for u, w in enumerate(_DA_UNITS, 1)}
        out[f"etog{tens}"] = n + 1
    return out


WORDS: Final[dict[str, int]] = _words()
_WORD: Final = "|".join(sorted(map(re.escape, WORDS), key=len, reverse=True))
_DIGITS: Final = r"\d+(?::\d\d|[.,]\d+(?![.,]\d))?"
_TOKEN: Final = re.compile(rf"(?<!\w)(?:(?P<d>{_DIGITS})|(?P<w>{_WORD})(?!\w))")


@dataclass(frozen=True, slots=True)
class NumberToken:
    """One number in a cue: where it is, its value (minutes for a clock "1:30") and its kind."""

    start: int
    end: int
    value: float
    word: bool
    ambiguous: bool
    clock: bool = False


def _digits(text: str) -> tuple[float, bool]:
    if ":" in text:
        return float(text.split(":")[0]), True
    return float(text.replace(",", ".")), False


def number_tokens(text: str) -> list[NumberToken]:
    """Every number in casefolded ``text``, in order."""
    out: list[NumberToken] = []
    for m in _TOKEN.finditer(text):
        if m.group("d") is not None:
            value, clock = _digits(m.group("d"))
            out.append(NumberToken(m.start(), m.end(), value, False, False, clock))
        else:
            w = m.group("w")
            out.append(NumberToken(m.start(), m.end(), WORDS[w], True, w in AMBIGUOUS))
    return out

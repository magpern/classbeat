"""Class titles (pure): the cleaner every title a duplicate stores goes through, and the short name
the interpreter may propose for a new class (issue #1233).

- ``clean_title``: line breaks, tabs and other blanks turned into spaces, control and format
  characters removed and blanks collapsed, 1 to ``TITLE_MAX`` characters (split out of
  ``classbeat.classes.duplicate_request``, which still exports it).
- ``proposed_name``: the interpreter's ``name`` as a title, or ``None``. It is the model's text,
  so it is display text only: it never decides anything and never goes back to a model. Emoji and
  other symbols (Unicode ``So`` and ``Sk``), variation selectors (and their supplement), the
  combining keycap, the Hangul fillers that look blank and control and format characters are
  dropped, and every quote mark but an apostrophe, then any quote around it, then ``clean_title``;
  so the result is its own cleaning (``proposed_name`` is idempotent, #1237). What is left must
  hold a letter or a digit, be at most ``NAME_MAX`` characters **after** the cleaning (so quotes
  or emoji alone never make a name too long) and look like no link (``LINK``: a scheme,
  ``www.``, an ``@`` or a ``word.tld`` domain, #1237). Apostrophes inside it stay ("Don't stop").
"""

import re
import unicodedata
from typing import Final

TITLE_MAX: Final = 100  # the classes_title check of migration 0004
NAME_MAX: Final = 60  # what the interpreter is asked for (agent.interpret)
APOSTROPHES: Final = frozenset({"'", "\u2019"})  # kept inside a name ("Don't stop")
# Quote marks besides the initial and final ones (Unicode Pi, Pf): ASCII, low-9 and CJK corners.
OTHER_QUOTES: Final = frozenset("\"'`\u00b4\u201a\u201e\u300c\u300d\u300e\u300f")
DROPPED_CATEGORIES: Final = frozenset({"So", "Sk"})  # emoji and other symbols, modifiers
# Characters that show nothing but are no blank or format character (#1237): the Hangul fillers,
# the combining enclosing keycap (an emoji's last part); variation selectors are in ``_shown``.
INVISIBLE: Final = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0", "\u20e3"})
# Link-like text: a scheme, "www.", an e-mail's "@", or a domain: two or more word characters, a
# dot, two or more letters ("evil.com"; "t.ex", "bl.a", "St. Louis" and "3.5" are not).
LINK: Final = re.compile(r"://|\bwww\.|@|\b[\w-]{2,}\.[a-z]{2,24}\b", re.IGNORECASE)


def clean_title(raw: str) -> str | None:
    """The title with line breaks, tabs and other blanks turned into spaces, control and format
    characters removed and blanks collapsed, 1 to 100 characters; ``None`` otherwise."""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in raw
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    title = " ".join(kept.split())
    return title if 1 <= len(title) <= TITLE_MAX else None


def _quote(ch: str) -> bool:
    return ch in OTHER_QUOTES or unicodedata.category(ch) in ("Pi", "Pf")


def _shown(ch: str) -> bool:
    if ch in INVISIBLE or "\ufe00" <= ch <= "\ufe0f" or "\U000e0100" <= ch <= "\U000e01ef":
        return False
    category = unicodedata.category(ch)
    if category in DROPPED_CATEGORIES or (category[0] == "C" and not ch.isspace()):
        return False  # control and format characters go first, so a quote beside one is stripped
    return ch in APOSTROPHES or not _quote(ch)


def proposed_name(raw: str | None) -> str | None:
    """The interpreter's short name for a new class, cleaned as a title, or ``None`` (none given,
    nothing left, longer than ``NAME_MAX`` once cleaned, or link-like)."""
    if raw is None:
        return None
    kept = "".join(ch for ch in raw if _shown(ch))
    while kept and (kept[0].isspace() or _quote(kept[0])):
        kept = kept[1:]
    while kept and (kept[-1].isspace() or _quote(kept[-1])):
        kept = kept[:-1]
    title = clean_title(kept)
    if title is None or len(title) > NAME_MAX or not any(ch.isalnum() for ch in title):
        return None
    return None if LINK.search(title) else title

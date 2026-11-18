"""Class titles (pure): the cleaner every title a duplicate stores goes through, and the short name
the interpreter may propose for a new class (issue #1233).

- ``clean_title``: line breaks, tabs and other blanks turned into spaces, control and format
  characters removed and blanks collapsed, 1 to ``TITLE_MAX`` characters (split out of
  ``classbeat.classes.duplicate_request``, which still exports it).
- ``proposed_name``: the interpreter's ``name`` as a title, or ``None``. It is the model's text,
  so it is display text only: it never decides anything and never goes back to a model. Emoji and
  other symbols (Unicode ``So`` and ``Sk``, variation selectors) are dropped, and every quote mark
  but an apostrophe, and any quote around it, then ``clean_title``; it must hold a letter or a
  digit and be at most ``NAME_MAX`` characters. Apostrophes inside it stay ("Don't stop").
"""

import unicodedata
from typing import Final

TITLE_MAX: Final = 100  # the classes_title check of migration 0004
NAME_MAX: Final = 60  # what the interpreter is asked for (agent.interpret)
APOSTROPHES: Final = frozenset({"'", "\u2019"})  # kept inside a name ("Don't stop")
# Quote marks besides the initial and final ones (Unicode Pi, Pf): ASCII, low-9 and CJK corners.
OTHER_QUOTES: Final = frozenset("\"'`\u00b4\u201a\u201e\u300c\u300d\u300e\u300f")
DROPPED_CATEGORIES: Final = frozenset({"So", "Sk"})  # emoji and other symbols, modifiers


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
    if "\ufe00" <= ch <= "\ufe0f" or unicodedata.category(ch) in DROPPED_CATEGORIES:
        return False
    return ch in APOSTROPHES or not _quote(ch)


def proposed_name(raw: str | None) -> str | None:
    """The interpreter's short name for a new class, cleaned as a title, or ``None`` (none given,
    nothing left, or longer than ``NAME_MAX``)."""
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
    return title

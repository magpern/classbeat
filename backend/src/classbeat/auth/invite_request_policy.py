"""The rules of an invite request (ADR 0033 sections 2 and 3). Pure: no database, no I/O.

``heard_from`` is the visitor's own answer to "where did you hear about classbeat". It is text, not
markup: shown as text in the admin area, never rendered as HTML, never sent to a model, never in a
log or an audit row. Whitespace runs (newlines and tabs included) become one space; a control,
format, surrogate, private-use or unassigned character is refused (bidi overrides and zero-width
characters could disguise the text on the admin's screen). At most ``HEARD_FROM_MAX`` characters.
"""

import unicodedata
from typing import Final

HEARD_FROM_MAX: Final = 300
# A decided request goes 30 days after the decision (the invite's own retention); a pending one
# 90 days after it was made, so an unanswered address is not kept for ever.
DECIDED_RETENTION_DAYS: Final = 30
PENDING_RETENTION_DAYS: Final = 90
# At most this many pending requests are stored; beyond it a request is dropped (the answer is
# the same), so a flood cannot grow the table without bound.
PENDING_CAP: Final = 1000

_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def clean_heard_from(value: str) -> str:
    """The stored form of ``heard_from``; raises ``ValueError`` for a refused character or when
    it is too long."""
    cleaned = " ".join(value.split())
    if len(cleaned) > HEARD_FROM_MAX:
        raise ValueError("heard_from is too long")
    if any(unicodedata.category(char) in _REFUSED_CATEGORIES for char in cleaned):
        raise ValueError("heard_from holds a character that is not shown as text")
    return cleaned

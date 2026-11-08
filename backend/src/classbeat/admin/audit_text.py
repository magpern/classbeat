"""Free text the admin area accepts (ADR 0011 sections 5 and 6, #227): the validators that keep it
safe to store in ``admin_audit.params`` or to show to admins again, and the bounded text types the
feature modules use for their fields. Pure: no I/O. ``classbeat.admin.audit`` holds the action
registry these bounds serve (``MAX_PARAMS_BYTES``).
"""

import json
import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator, StringConstraints


def json_bytes(value: str) -> int:
    """How many bytes ``value`` takes inside a JSON string as ``checked_params`` writes it
    (escapes included: a non-ASCII character is 6 or 12 bytes, not 1)."""
    return len(json.dumps(value).encode()) - 2


def bounded_text(max_bytes: int) -> AfterValidator:
    """Free text that is safe to store in ``params``: no control character (Unicode ``Cc``, NUL
    included, which ``jsonb`` refuses) and at most ``max_bytes`` once JSON-encoded, so a bound in
    characters can never push the params past ``MAX_PARAMS_BYTES``."""

    def check(value: str) -> str:
        if any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("control characters are not allowed")
        if json_bytes(value) > max_bytes:
            raise ValueError(f"at most {max_bytes} bytes once encoded")
        return value

    return AfterValidator(check)


# Format characters (Unicode ``Cf``: the bidi embeddings, overrides and isolates U+202A-202E and
# U+2066-2069, zero-width characters, the BOM) and the line and paragraph separators: they change
# how text shown in the admin pages reads without being visible themselves (#227, #279 review).
_INVISIBLE_CATEGORIES: Final = frozenset({"Cf", "Zl", "Zp"})


def no_format_characters() -> AfterValidator:
    """Text an admin types to be shown to admins again (a code's label, a grant's note): no
    format or separator character, checked apart from ``bounded_text``'s control characters."""

    def check(value: str) -> str:
        if any(unicodedata.category(c) in _INVISIBLE_CATEGORIES for c in value):
            raise ValueError("format characters (bidi, zero-width) are not allowed")
        return value

    return AfterValidator(check)


# Text an admin types that admins see again: no control character, bounded in encoded bytes (12
# per character at most), and no format character. A code's label (AD2, trimmed) and a grant's
# note (AD1).
LabelText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    bounded_text(960),
    no_format_characters(),
]
NoteText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    bounded_text(2400),
    no_format_characters(),
]


# The Users list's search text (AD1 #218): no control character, and bounded in encoded bytes as
# well as in characters (254 emoji would be 3048 bytes once encoded).
UserQuery = Annotated[str, StringConstraints(max_length=254), bounded_text(1024)]


# The audit filter's two free-text fields, bounded so the largest accepted filter still fits
# MAX_PARAMS_BYTES (``tests/test_admin_audit.py`` measures the worst case).
FilterEmail = Annotated[str, StringConstraints(min_length=1, max_length=320), bounded_text(640)]
FilterTargetId = Annotated[str, StringConstraints(min_length=1, max_length=100), bounded_text(200)]

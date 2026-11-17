"""The mail settings' read and write rules (ADR 0022 sections 2 and 6, #653; pure).

``mail_sender_name``, ``mail_support_email``, ``mail_footer`` and ``mail_kinds_disabled`` are
the mail branding and the admin's switched-off kinds. They are no flags (``core.MAIL_KEYS``, not
``core.KEYS``): the Mail page edits them. A missing, ``null`` or invalid stored value means the
environment's value (branding) or nothing disabled; only an ``admin_can_disable`` kind id counts
as disabled. Moved out of ``classbeat.flags.core`` unchanged (#1185).
"""

import re
from collections.abc import Mapping
from typing import Any, Final

from classbeat.flags.core import FlagRefused, invalid, plain_text

MAIL_SENDER_NAME_MAX: Final = 60
MAIL_FOOTER_MAX: Final = 280
_KIND_ID: Final = re.compile(r"^[a-z][a-z_]{0,39}$")
# The same shape as ``Settings.mail_support_address``: a bare address, no display name.
_SUPPORT_ADDRESS: Final = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


def _short_text(value: Any, limit: int) -> str | None:
    """``value`` when it is non-blank plain text of at most ``limit`` characters, else ``None``."""
    if isinstance(value, str) and value.strip() and len(value) <= limit and plain_text(value):
        return value
    return None


def mail_sender_name_setting(stored: Any) -> str | None:
    """The stored From display name; ``None`` (the default name) when absent or invalid."""
    if stored is None:
        return None
    text = _short_text(stored, MAIL_SENDER_NAME_MAX)
    if text is None:
        invalid("mail_sender_name")
    return text


def mail_support_email_setting(stored: Any) -> str | None:
    """The stored support address; ``None`` (the environment's) when absent or invalid."""
    if stored is None:
        return None
    if isinstance(stored, str) and _SUPPORT_ADDRESS.fullmatch(stored):
        return stored
    invalid("mail_support_email")
    return None


def mail_footer_setting(stored: Any) -> str | None:
    """The stored footer line; ``None`` (no footer) when absent or invalid."""
    if stored is None:
        return None
    text = _short_text(stored, MAIL_FOOTER_MAX)
    if text is None:
        invalid("mail_footer")
    return text


def mail_kinds_disabled_setting(stored: Any, can_disable: frozenset[str]) -> frozenset[str]:
    """The stored kind ids that are switched off: only ids in ``can_disable`` (the registry's
    ``admin_can_disable`` kinds) count, so a stale or invalid row never silences a mail that must
    go out. Absent is none."""
    if stored is None:
        return frozenset()
    if not isinstance(stored, list) or not all(isinstance(i, str) for i in stored):  # pyright: ignore[reportUnknownVariableType]
        invalid("mail_kinds_disabled")
        return frozenset()
    return frozenset(stored) & can_disable  # pyright: ignore[reportUnknownArgumentType]


def validate_mail(key: str, value: Any, kinds: Mapping[str, bool]) -> Any:
    """The canonical value of a mail setting, or ``FlagRefused`` (``core.validate_for_write``'s
    rules for the mail keys)."""
    if key == "mail_sender_name":
        return _mail_text(value, MAIL_SENDER_NAME_MAX)
    if key == "mail_footer":
        return _mail_text(value, MAIL_FOOTER_MAX)
    if key == "mail_support_email":
        if not isinstance(value, str) or not _SUPPORT_ADDRESS.fullmatch(value):
            raise FlagRefused("invalid_value")
        return value
    if key == "mail_kinds_disabled":
        return _mail_kinds(value, kinds)
    raise FlagRefused("unknown_key")


def _mail_text(value: Any, limit: int) -> str:
    text = _short_text(value, limit)
    if text is None:
        raise FlagRefused("invalid_value")
    return text


def _mail_kinds(value: Any, kinds: Mapping[str, bool]) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:  # pyright: ignore[reportUnknownArgumentType]
        raise FlagRefused("invalid_value")
    ids: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
    if not all(isinstance(i, str) and _KIND_ID.fullmatch(i) for i in ids) or len(set(ids)) != len(
        ids
    ):
        raise FlagRefused("invalid_value")
    for kind_id in ids:
        if kind_id not in kinds:
            raise FlagRefused("unknown_kind")
        if not kinds[kind_id]:
            raise FlagRefused("essential_kind")
    return sorted(ids)

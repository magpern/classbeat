"""The flag keys, their validation and the stricter-wins rule (ADR 0011 section 7).

Pure functions: no database, no environment. ``classbeat.flags.store`` reads the rows and
``classbeat.flags.effective`` joins them with the environment.

The database can tighten what the environment allows, never loosen it:

- ``signup_mode``: stored ``invite`` or ``closed``; the effective mode is the stricter of the
  environment's and the stored one (``closed`` above ``invite`` above ``open``). ``open`` comes
  only from ``CLASSBEAT_SIGNUP_MODE``; a stored ``open`` is invalid.
- ``song_picking``: stored ``off``, ``granted`` or ``on``; absent means ``granted``.
- ``swedebeat_enabled``: stored ``off`` or ``on``; absent means ``on``, but only under the
  environment's kill switch (``CLASSBEAT_SWEDEBEAT_ENABLED``, off by default): the database
  can switch Swedebeat off for everyone, never on past the environment (ADR 0015).
- ``composer_flow`` (ADR 0038 section 2, #1185): stored ``off`` or ``on``, under
  ``CLASSBEAT_COMPOSER_FLOW`` (off by default), but **unlike** ``swedebeat_enabled`` a missing,
  ``null`` or invalid stored value is ``off``: only a stored ``on`` switches it on, so clearing the
  row turns it off (``composer_switch``).
- Running and soundless classes have no stored key and no switch (ADR 0018 Amendment 6, #1544:
  running is always available; ADR 0007 amendments of 2026-11-08, #824, and 2026-11-27, #1543:
  "No music" is always a choice). Migration 0082 removed the ``running_enabled`` key.
- ``trial``: ``{plan, days}``; absent or invalid is off.
- ``banner``: ``{text, level}``; absent or invalid is none. Its text is plain text: no control
  character (``Cc``), no format character (``Cf``: the bidi embeddings, overrides and isolates,
  zero-width characters, the soft hyphen, the BOM) and no line or paragraph separator (``Zl``,
  ``Zp``), on the write side and again when it is read for ``/api/settings/public`` (#227).

The mail settings' rules are ``classbeat.flags.mail_settings``.

A stored value that does not validate falls back to the key's default, which is its stricter
choice (``invite``, ``granted``, Swedebeat off, composer off, trial off, no banner), and is
logged by key only: the value is never logged, since it may hold an admin's free text.
"""

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

logger = logging.getLogger(__name__)

SignupMode = Literal["invite", "closed", "open"]
SongPicking = Literal["off", "granted", "on"]
BannerLevel = Literal["info", "warning"]

BANNER_MAX_CHARS: Final = 280
_STRICTNESS: Final[dict[str, int]] = {"open": 0, "invite": 1, "closed": 2}
_SIGNUP_STORED: Final = ("invite", "closed")
_SONG_STORED: Final = ("off", "granted", "on")
_SWEDEBEAT_STORED: Final = ("off", "on")
_ON_OFF: Final = ("off", "on")  # composer_flow
_LEVELS: Final = ("info", "warning")
# What banner text may not hold (#227, the #303 review's note 5 for the admin's free text): the
# control characters, the format characters (bidi controls such as U+202E, which reorder what
# the page shows; zero-width characters, which hide what it holds) and the line and paragraph
# separators. Ordinary letters, marks, symbols and emoji are kept.
_NOT_PLAIN_TEXT: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


def plain_text(text: str) -> bool:
    return not any(unicodedata.category(c) in _NOT_PLAIN_TEXT for c in text)


@dataclass(frozen=True, slots=True)
class Banner:
    text: str
    level: BannerLevel


@dataclass(frozen=True, slots=True)
class TrialSetting:
    plan: str
    days: int


def invalid(key: str) -> None:
    logger.warning("flag %s holds an invalid value; its stricter default applies", key)


def stricter_signup(environment: SignupMode, stored: Any) -> SignupMode:
    """``stored`` is the row's value or ``None`` when there is no row."""
    if stored is None:
        return environment
    if not isinstance(stored, str) or stored not in _SIGNUP_STORED:
        invalid("signup_mode")
        stored = "invite"
    pick = stored if _STRICTNESS[stored] > _STRICTNESS[environment] else environment
    return pick  # pyright: ignore[reportReturnType]


def song_picking_setting(environment_on: bool, stored: Any) -> SongPicking:
    """``off`` while the environment's kill switch is off; else the stored value (default
    ``granted``)."""
    if not environment_on:
        return "off"
    if stored is None:
        return "granted"
    if not isinstance(stored, str) or stored not in _SONG_STORED:
        invalid("song_picking")
        return "granted"
    return stored  # pyright: ignore[reportReturnType]


def swedebeat_setting(environment_on: bool, stored: Any) -> bool:
    """Whether the Swedebeat connection is on: never while the environment's switch is off; else
    the stored ``off``/``on`` (absent is on; an invalid value is off, the stricter reading)."""
    if not environment_on:
        return False
    if stored is None:
        return True
    if not isinstance(stored, str) or stored not in _SWEDEBEAT_STORED:
        invalid("swedebeat_enabled")
        return False
    return stored == "on"


def composer_switch(environment_on: bool, stored: Any) -> bool:
    """``composer_flow`` (ADR 0038 section 2): the environment's switch is on **and** the stored
    value is exactly ``on``. Absent (``None``, also the cleared JSON ``null``) is off; an invalid
    value is off and logged by key."""
    return _stored_on("composer_flow", environment_on, stored)


def _stored_on(key: str, environment_on: bool, stored: Any) -> bool:
    if not environment_on or stored is None:
        return False
    if not isinstance(stored, str) or stored not in _ON_OFF:
        invalid(key)
        return False
    return stored == "on"


def trial_setting(stored: Any) -> TrialSetting | None:
    """The stored ``{plan, days}``; ``None`` (off) when absent, malformed or ``days`` is 0."""
    if stored is None:
        return None
    if isinstance(stored, dict):
        plan, days = stored.get("plan"), stored.get("days")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(plan, str) and plan and type(days) is int and days >= 0:
            return TrialSetting(plan, days) if days > 0 else None
    invalid("trial")
    return None


def banner_setting(stored: Any) -> Banner | None:
    """The stored ``{text, level}``; ``None`` when absent, empty, too long, not plain text
    (a value stored before the write side refused it, or written past it) or malformed."""
    if stored is None:
        return None
    if isinstance(stored, dict):
        text, level = stored.get("text"), stored.get("level")  # pyright: ignore[reportUnknownMemberType]
        if (
            isinstance(text, str)
            and level in _LEVELS
            and len(text) <= BANNER_MAX_CHARS
            and plain_text(text)
        ):
            return Banner(text, level) if text.strip() else None  # pyright: ignore[reportArgumentType]
    invalid("banner")
    return None


# --- validation at write time (the admin area, ADR 0011 section 6) ---------------------------


class FlagRefused(Exception):
    """A value the admin may not store: ``reason`` is the audit and response code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


KEYS: Final = (
    "signup_mode",
    "song_picking",
    "swedebeat_enabled",
    "composer_flow",
    "trial",
    "banner",
)
MAIL_KEYS: Final = ("mail_sender_name", "mail_support_email", "mail_footer", "mail_kinds_disabled")
_PLAN_ID: Final = re.compile(r"^[a-z_]{1,30}$")


def validate_for_write(
    key: str,
    value: Any,
    *,
    trial_days_max: int,
    mail_kinds: Mapping[str, bool] | None = None,
) -> Any:
    """The canonical value to store for ``key``, or ``FlagRefused``. Stricter than the read side:
    the read side falls back, the write side refuses.

    - ``signup_mode``: ``invite`` or ``closed``; ``open`` comes only from the environment
      (``environment_only``).
    - ``song_picking``: ``off``, ``granted`` or ``on``.
    - ``swedebeat_enabled`` and ``composer_flow``: ``off`` or ``on``.
    - ``trial``: exactly ``{plan, days}``, a plan code and ``days`` from 0 to ``trial_days_max``
      (``above_environment_bound`` over it). Whether the plan exists and ranks at most
      ``instructor`` is checked against ``plans`` by the caller.
    - ``banner``: exactly ``{text, level}``; plain text of 1 to 280 characters (no control,
      format or separator character), level ``info`` or ``warning``.
    - ``mail_sender_name`` (1 to 60) and ``mail_footer`` (1 to 280): plain text, the banner's
      rules. ``mail_support_email``: a bare address. ``mail_kinds_disabled``: a list of distinct
      kind ids, each in ``mail_kinds`` (kind id -> whether the admin may disable it; an
      essential kind is
      ``essential_kind``, an id that is no kind ``unknown_kind``); an empty list is allowed."""
    if key == "signup_mode":
        if value == "open":
            raise FlagRefused("environment_only")
        if value not in _SIGNUP_STORED:
            raise FlagRefused("invalid_value")
        return value
    if key == "song_picking":
        if value not in _SONG_STORED:
            raise FlagRefused("invalid_value")
        return value
    if key == "swedebeat_enabled":
        if value not in _SWEDEBEAT_STORED:
            raise FlagRefused("invalid_value")
        return value
    if key == "composer_flow":
        if not isinstance(value, str) or value not in _ON_OFF:
            raise FlagRefused("invalid_value")
        return value
    if key == "trial":
        if not isinstance(value, dict) or set(value) != {"plan", "days"}:  # pyright: ignore[reportUnknownArgumentType]
            raise FlagRefused("invalid_value")
        plan, days = value["plan"], value["days"]
        if not isinstance(plan, str) or not _PLAN_ID.fullmatch(plan):
            raise FlagRefused("invalid_value")
        if type(days) is not int or days < 0:
            raise FlagRefused("invalid_value")
        if days > trial_days_max:
            raise FlagRefused("above_environment_bound")
        return {"plan": plan, "days": days}
    if key == "banner":
        if not isinstance(value, dict) or set(value) != {"text", "level"}:  # pyright: ignore[reportUnknownArgumentType]
            raise FlagRefused("invalid_value")
        text, level = value["text"], value["level"]
        if not isinstance(text, str) or level not in _LEVELS or not text.strip():
            raise FlagRefused("invalid_value")
        if len(text) > BANNER_MAX_CHARS or not plain_text(text):
            raise FlagRefused("invalid_value")
        return {"text": text, "level": level}
    if key in MAIL_KEYS:
        from classbeat.flags.mail_settings import validate_mail  # it imports this module

        return validate_mail(key, value, mail_kinds or {})
    raise FlagRefused("unknown_key")

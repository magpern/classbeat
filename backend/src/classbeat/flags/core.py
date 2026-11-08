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
- ``running_enabled`` (ADR 0018 section 5 and its amendment of 2026-11-04, #509): stored ``off``
  or ``on``, under ``CLASSBEAT_RUNNING_ENABLED`` (off by default), but **unlike**
  ``swedebeat_enabled`` a missing, ``null`` or invalid stored value is ``off``: only a stored
  ``on`` switches it on, so clearing the row turns it off. ``running_switch`` is that part; the
  effective flag also needs every plan's model route to cover running
  (``classbeat.flags.effective.running_enabled``). Soundless classes have no stored key (ADR 0007
  amendments of 2026-11-03 and 2026-11-08, #824): ``classbeat.flags.effective.soundless_enabled``
  is the environment's switch and coverage only.
- ``trial``: ``{plan, days}``; absent or invalid is off.
- ``banner``: ``{text, level}``; absent or invalid is none. Its text is plain text: no control
  character (``Cc``), no format character (``Cf``: the bidi embeddings, overrides and isolates,
  zero-width characters, the soft hyphen, the BOM) and no line or paragraph separator (``Zl``,
  ``Zp``), on the write side and again when it is read for ``/api/settings/public`` (#227).

- ``mail_sender_name``, ``mail_support_email``, ``mail_footer`` and ``mail_kinds_disabled``
  (ADR 0022 sections 2 and 6, #653): the mail branding and the admin's switched-off kinds. They
  are no flags (``MAIL_KEYS``, not ``KEYS``): the Mail page edits them. A missing, ``null`` or
  invalid stored value means the environment's value (branding) or nothing disabled; only an
  ``admin_can_disable`` kind id counts as disabled.

A stored value that does not validate falls back to the key's default, which is its stricter
choice (``invite``, ``granted``, Swedebeat off, running off, trial off, no banner), and is logged
by key only: the value is never logged, since it may hold an admin's free text.
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
_RUNNING_STORED: Final = ("off", "on")
_LEVELS: Final = ("info", "warning")
# What banner text may not hold (#227, the #303 review's note 5 for the admin's free text): the
# control characters, the format characters (bidi controls such as U+202E, which reorder what
# the page shows; zero-width characters, which hide what it holds) and the line and paragraph
# separators. Ordinary letters, marks, symbols and emoji are kept.
_NOT_PLAIN_TEXT: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _plain_text(text: str) -> bool:
    return not any(unicodedata.category(c) in _NOT_PLAIN_TEXT for c in text)


@dataclass(frozen=True, slots=True)
class Banner:
    text: str
    level: BannerLevel


@dataclass(frozen=True, slots=True)
class TrialSetting:
    plan: str
    days: int


def _invalid(key: str) -> None:
    logger.warning("flag %s holds an invalid value; its stricter default applies", key)


def stricter_signup(environment: SignupMode, stored: Any) -> SignupMode:
    """``stored`` is the row's value or ``None`` when there is no row."""
    if stored is None:
        return environment
    if not isinstance(stored, str) or stored not in _SIGNUP_STORED:
        _invalid("signup_mode")
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
        _invalid("song_picking")
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
        _invalid("swedebeat_enabled")
        return False
    return stored == "on"


def running_switch(environment_on: bool, stored: Any) -> bool:
    """The switch part of ``running_enabled`` (ADR 0018 amendment of 2026-11-04, #509): the
    environment's switch is on **and** the stored value is exactly ``on``. Absent (``None``, also
    the cleared JSON ``null``) is off; an invalid value is off and logged by key. Coverage is not
    checked here (``flags.effective``)."""
    if not environment_on or stored is None:
        return False
    if not isinstance(stored, str) or stored not in _RUNNING_STORED:
        _invalid("running_enabled")
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
    _invalid("trial")
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
            and _plain_text(text)
        ):
            return Banner(text, level) if text.strip() else None  # pyright: ignore[reportArgumentType]
    _invalid("banner")
    return None


def _short_text(value: Any, limit: int) -> str | None:
    """``value`` when it is non-blank plain text of at most ``limit`` characters, else ``None``."""
    if isinstance(value, str) and value.strip() and len(value) <= limit and _plain_text(value):
        return value
    return None


def mail_sender_name_setting(stored: Any) -> str | None:
    """The stored From display name; ``None`` (the default name) when absent or invalid."""
    if stored is None:
        return None
    text = _short_text(stored, MAIL_SENDER_NAME_MAX)
    if text is None:
        _invalid("mail_sender_name")
    return text


def mail_support_email_setting(stored: Any) -> str | None:
    """The stored support address; ``None`` (the environment's) when absent or invalid."""
    if stored is None:
        return None
    if isinstance(stored, str) and _SUPPORT_ADDRESS.fullmatch(stored):
        return stored
    _invalid("mail_support_email")
    return None


def mail_footer_setting(stored: Any) -> str | None:
    """The stored footer line; ``None`` (no footer) when absent or invalid."""
    if stored is None:
        return None
    text = _short_text(stored, MAIL_FOOTER_MAX)
    if text is None:
        _invalid("mail_footer")
    return text


def mail_kinds_disabled_setting(stored: Any, can_disable: frozenset[str]) -> frozenset[str]:
    """The stored kind ids that are switched off: only ids in ``can_disable`` (the registry's
    ``admin_can_disable`` kinds) count, so a stale or invalid row never silences a mail that must
    go out. Absent is none."""
    if stored is None:
        return frozenset()
    if not isinstance(stored, list) or not all(isinstance(i, str) for i in stored):  # pyright: ignore[reportUnknownVariableType]
        _invalid("mail_kinds_disabled")
        return frozenset()
    return frozenset(stored) & can_disable  # pyright: ignore[reportUnknownArgumentType]


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
    "running_enabled",
    "trial",
    "banner",
)
MAIL_KEYS: Final = ("mail_sender_name", "mail_support_email", "mail_footer", "mail_kinds_disabled")
MAIL_SENDER_NAME_MAX: Final = 60
MAIL_FOOTER_MAX: Final = 280
_KIND_ID: Final = re.compile(r"^[a-z][a-z_]{0,39}$")
# The same shape as ``Settings.mail_support_address``: a bare address, no display name.
_SUPPORT_ADDRESS: Final = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
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
    - ``swedebeat_enabled`` and ``running_enabled``: ``off`` or ``on``.
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
    if key == "running_enabled":
        if not isinstance(value, str) or value not in _RUNNING_STORED:
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
        if len(text) > BANNER_MAX_CHARS or not _plain_text(text):
            raise FlagRefused("invalid_value")
        return {"text": text, "level": level}
    if key == "mail_sender_name":
        return _mail_text(value, MAIL_SENDER_NAME_MAX)
    if key == "mail_footer":
        return _mail_text(value, MAIL_FOOTER_MAX)
    if key == "mail_support_email":
        if not isinstance(value, str) or not _SUPPORT_ADDRESS.fullmatch(value):
            raise FlagRefused("invalid_value")
        return value
    if key == "mail_kinds_disabled":
        return _mail_kinds(value, mail_kinds or {})
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

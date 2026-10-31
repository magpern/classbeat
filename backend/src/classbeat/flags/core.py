"""The flag keys, their validation and the stricter-wins rule (ADR 0011 section 7).

Pure functions: no database, no environment. ``classbeat.flags.store`` reads the rows and
``classbeat.flags.effective`` joins them with the environment.

The database can tighten what the environment allows, never loosen it:

- ``signup_mode``: stored ``invite`` or ``closed``; the effective mode is the stricter of the
  environment's and the stored one (``closed`` above ``invite`` above ``open``). ``open`` comes
  only from ``CLASSBEAT_SIGNUP_MODE``; a stored ``open`` is invalid.
- ``song_picking``: stored ``off``, ``granted`` or ``on``; absent means ``granted``.
- ``trial``: ``{plan, days}``; absent or invalid is off.
- ``banner``: ``{text, level}``; absent or invalid is none.

A stored value that does not validate falls back to the key's default, which is its stricter
choice (``invite``, ``granted``, trial off, no banner), and is logged by key only: the value is
never logged, since it may hold an admin's free text.
"""

import logging
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
_LEVELS: Final = ("info", "warning")


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
    """The stored ``{text, level}``; ``None`` when absent, empty, too long or malformed."""
    if stored is None:
        return None
    if isinstance(stored, dict):
        text, level = stored.get("text"), stored.get("level")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(text, str) and level in _LEVELS and len(text) <= BANNER_MAX_CHARS:
            return Banner(text, level) if text.strip() else None  # pyright: ignore[reportArgumentType]
    _invalid("banner")
    return None

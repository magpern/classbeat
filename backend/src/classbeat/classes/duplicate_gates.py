"""The feature gates of a duplicate (ADR 0007 amendment of 2026-11-03, part D, item 2), split out
of ``classbeat.classes.duplicate``: the soundless and running designs' own gates and the Swedebeat
flag for a class or draft on a mix. Reads only, on the duplicate's own session.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.design import sport as sport_gate
from classbeat.design.mixadmit import MIX_SOURCE
from classbeat.flags.effective import swedebeat_enabled


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


async def flag_refusal(
    db: AsyncSession,
    settings: Settings,
    ctx: UserContext,
    sport: str,
    music_source: Any,
    music: Any,
) -> str | None:
    """The reason a flag refuses this copy, or None (item 2 of ``classbeat.classes.duplicate``)."""
    if music_kind.is_none(music_source) or music_kind.is_none(music):
        refused = await music_kind.admit_soundless(db, ctx)  # the soundless design's own gate
        if refused is not None:
            return refused.reason
    if sport == "running":
        refused = await sport_gate.admit_running(db, ctx)  # the running design's own gate (#509)
        if refused is not None:
            return refused.reason
    on_mix = (
        _dict(music_source).get("source") == MIX_SOURCE or _dict(music).get("source") == MIX_SOURCE
    )
    if on_mix and not await swedebeat_enabled(db, settings.swedebeat_enabled):
        return "feature_off"
    return None

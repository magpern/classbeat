"""The feature gates of a duplicate (ADR 0007 amendment of 2026-11-03, part D, item 2), split out
of ``classbeat.classes.duplicate``: the running design's own gate and the Swedebeat flag for a
class or draft on a mix. Reads only, on the duplicate's own session. A soundless copy has no gate
(ADR 0007 amendment of 2026-11-27, #1543: "No music" is always a choice).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.context import UserContext
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

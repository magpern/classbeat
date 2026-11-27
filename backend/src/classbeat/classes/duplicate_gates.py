"""The feature gate of a duplicate (ADR 0007 amendment of 2026-11-03, part D, item 2), split out
of ``classbeat.classes.duplicate``: the Swedebeat flag for a class or draft on a mix. Reads only,
on the duplicate's own session. A soundless copy has no gate (ADR 0007 amendment of 2026-11-27,
#1543: "No music" is always a choice), and neither has a running one (ADR 0018 Amendment 6,
#1544: running is always available).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.config import Settings
from classbeat.design.mixadmit import MIX_SOURCE
from classbeat.flags.effective import swedebeat_enabled


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


async def flag_refusal(
    db: AsyncSession,
    settings: Settings,
    music_source: Any,
    music: Any,
) -> str | None:
    """The reason a flag refuses this copy, or None (item 2 of ``classbeat.classes.duplicate``)."""
    on_mix = (
        _dict(music_source).get("source") == MIX_SOURCE or _dict(music).get("source") == MIX_SOURCE
    )
    if on_mix and not await swedebeat_enabled(db, settings.swedebeat_enabled):
        return "feature_off"
    return None

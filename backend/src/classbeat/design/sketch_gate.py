"""The composer's sketch gate (ADR 0038 amendment of 2026-11-24, #1484).

A sketch is built as a ``design_draft`` with ``NoMusicChoice`` in mode ``sketch`` (and revised by
the soundless ``revise`` stage), but it is never written anywhere (``revision_ready``). The
environment's soundless switch (``CLASSBEAT_SOUNDLESS_ENABLED``, ``music_kind.switch_on``)
protects only the unverified MOWL save and update with ``PlaylistID: -1`` (ADR 0007), so a sketch
does not read it. What it needs is a model evaluated for the no-songs prompt for **this
instructor's plan**: ``admit_sketch`` checks the job's own plan's ``design`` route only
(``music_kind.plan_covers``), not every plan's. The worker keeps ``music_kind.require_covered``
and drops ``still_on``.

Used at every sketch path and nowhere else: the first sketch's admit (``kind_music``), a sketch
edit's admit and ``can_change`` (``revise_sketch``), and the composer's availability
(``composer.composer_available``). A real no-music class keeps ``music_kind.admit_soundless``.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.jobs.kinds import Refused


async def admit_sketch(db: AsyncSession, ctx: UserContext) -> Refused | None:
    """``soundless_not_evaluated`` unless the job's own plan's route covers soundless; None
    admits. Reads only; reserves nothing, and never reads the soundless switch."""
    if not await music_kind.plan_covers(db, ctx):
        return Refused("soundless_not_evaluated")
    return None

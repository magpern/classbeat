"""The composer's sketch gate (ADR 0038 amendment of 2026-11-24, #1484).

A sketch is built as a ``design_draft`` with ``NoMusicChoice`` in mode ``sketch`` (and revised by
the soundless ``revise`` stage), but it is never written anywhere (``revision_ready``). What it
needs is a model evaluated for the no-songs prompt for **this instructor's plan**:
``admit_sketch`` checks the job's own plan's ``design`` route only (``music_kind.plan_covers``),
not every plan's. The worker keeps ``music_kind.require_covered`` for a sketch
(``music_kind.serve_model``). There is no soundless switch (retired by ADR 0007's amendment of
2026-11-27, #1543); a real no-music class has no gate of its own.

Used at every sketch path and nowhere else: the first sketch's admit (``kind_music``), a sketch
edit's admit and ``can_change`` (``revise_sketch``), and the composer's availability
(``composer.composer_available``).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.jobs.kinds import Refused


async def admit_sketch(db: AsyncSession, ctx: UserContext) -> Refused | None:
    """``soundless_not_evaluated`` unless the job's own plan's route covers soundless; None
    admits. Reads only; reserves nothing."""
    if not await music_kind.plan_covers(db, ctx):
        return Refused("soundless_not_evaluated")
    return None

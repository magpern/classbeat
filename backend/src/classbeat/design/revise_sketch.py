"""The base gates of ``revise_draft``'s ``admit`` (split out of ``classbeat.design.revise``, the
repository's split-on-touch rule), with the sketch base of ADR 0038 section 4 (#1186).

A **sketch** base (``revision_ready.is_sketch``: the composer's design with no songs, the class's
current revision until its songs are in) is revised as a sketch: its class has no music source
yet, so the music-source check does not apply, and its revise runs the soundless ``revise`` stage
(``revise_soundless``, producing a ``sketch``), behind the soundless gate its design passed
(``music_kind.admit_soundless``). It takes no credit and counts toward the class's revise limit
like any chat revision (``chat.create_state``).

Any other base keeps its gates in their order: ``not_changeable`` for a class without a music
source (a duplicate of a class on picked songs, #536), the running gate (``sport.admit_class``,
ADR 0018, #509), the soundless gate of a soundless class (``music_kind.admit_class``, #528).
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design import music_kind, sport
from classbeat.design.kind_music import has_music_source
from classbeat.design.revision_ready import is_sketch
from classbeat.jobs.kinds import Refused


class SketchBaseField(BaseModel):
    """``revise_draft``'s server field ``sketch_base`` (``admit`` sets it on a sketch base). An
    ordinary revise does not write it, so its stored input and request hash are as before."""

    sketch_base: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_sketch_base(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        out: dict[str, Any] = handler(self)
        if out.get("sketch_base") is None:
            out.pop("sketch_base", None)
        return out


async def admit_base(
    db: AsyncSession, ctx: UserContext, class_id: UUID, base: UUID
) -> tuple[bool, Refused | None]:
    """Whether the base is a sketch, and the refusal of the base's gates (None admits). Reads
    only; nothing is written before them. An edited Spotify draft is admitted like any other: its
    run carries the edits (``revise_base.RunBase.kept``, #1451)."""
    if await is_sketch(db, ctx.user_id, class_id, base):
        return True, await music_kind.admit_soundless(db, ctx)
    if not await has_music_source(db, ctx.user_id, class_id):
        return False, Refused("not_changeable")
    running = await sport.admit_class(db, ctx, class_id)
    if running is not None:
        return False, running
    return False, await music_kind.admit_class(db, ctx, class_id)


async def can_revise(
    db: AsyncSession, ctx: UserContext, class_id: UUID, current: UUID | None, sourced: bool
) -> bool:
    """What the history says of a revise of the class now (``can_change``), as ``admit_base``
    decides it: a sketch through the soundless gate, any other class with a music source through
    ``music_kind.admit_class``."""
    if current is not None and await is_sketch(db, ctx.user_id, class_id, current):
        return await music_kind.admit_soundless(db, ctx) is None
    return sourced and await music_kind.admit_class(db, ctx, class_id) is None

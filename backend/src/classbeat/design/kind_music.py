"""The music checks of ``design_draft``'s ``admit`` (split out of ``classbeat.design.kind``).

Reads only; the jobs API reserves the credit after. It takes plain values (not the kind's input
models) so it never imports ``kind``: a class's music kind never changes (#527), a soundless
class passes the soundless gate (the switch, the job's own plan's route, every plan's route) and a
Swedebeat mix only through ``mixadmit.admit_mix`` (the flag, the MOWL connection, the user's own
listing, the daily limit).
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.design import mixadmit, music_kind
from classbeat.design.interpret_music_policy import no_music_reason
from classbeat.jobs.kinds import Refused


async def admit_music_choice(
    db: AsyncSession,
    ctx: UserContext,
    *,
    soundless: bool,
    mix_id: str | None,
    class_id: UUID | None,
    mowl_connection_id: UUID,
    spotify_playlist: bool = False,
    spotify_optional: bool = False,
) -> Refused | None:
    """``spotify_playlist``: a cycling class on the user's Spotify playlist, which needs the
    Spotify connection (roadmap Amendment 25, as the interpret admit); ``spotify_optional``: the
    hand-off of an interpretation stored before Amendment 25, which has none."""
    refused = await music_kind.admit_music(db, ctx.user_id, soundless, class_id)
    if refused is not None:
        return refused
    if soundless:
        refused = await music_kind.admit_soundless(db, ctx)
        if refused is not None:
            return refused
    if spotify_playlist and not spotify_optional and ctx.connection("spotify") is None:
        # No model call and no credit; with song picking off there is nothing to connect.
        return Refused(no_music_reason(await song_picking(db, ctx)))
    if mix_id is not None:
        return await mixadmit.admit_mix(db, ctx, mix_id, mowl_connection_id)
    return None


async def song_picking(db: AsyncSession, ctx: UserContext) -> bool:
    """Whether song picking admits the user (the flag and its environment switch)."""
    # Imported here: ``classbeat.songs`` imports this package.
    from classbeat.flags import effective
    from classbeat.songs import config

    return await effective.song_picking_admits(db, ctx, config.current().enabled)


_MUSIC_SOURCE = text(
    "SELECT music_source IS NOT NULL FROM classes WHERE owner_id = :owner_id AND id = :class_id"
)


async def has_music_source(db: AsyncSession, owner_id: UUID, class_id: UUID) -> bool:
    found = await db.execute(_MUSIC_SOURCE, {"owner_id": owner_id, "class_id": class_id})
    return bool(found.scalar_one_or_none())

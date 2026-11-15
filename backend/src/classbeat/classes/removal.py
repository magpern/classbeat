"""The class delete's transaction (ADR 0007 sections 8 and 11, ADR 0034 section 5) and its
answer (``schemas.DeletedOut``). Split out of ``classbeat.classes.service``."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes import seams, song_views
from classbeat.classes.class_sql import LOCK_CLASS, OPEN_JOB_SQL
from classbeat.classes.schemas import DeletedOut, PlaylistNameOut
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.player_send.publications import on_class_delete

KEPT_NOTE = "The workouts classbeat saved to your MOWL account stay there; delete them in MOWL."
SPOTIFY_NOTE = (
    "The playlists classbeat made in your Spotify account stay there; delete them in Spotify."
)

_WRITES = text(
    f"SELECT state, (state = 'uncertain' AND {seams.NOT_ACTIONABLE_SQL}) AS inside_bound "  # noqa: S608 - constants only
    "FROM external_operations WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind IN ('mowl_save_workout', 'mowl_update_workout') "
    "AND state IN ('pending', 'uncertain')"
)
_KEPT = text(
    "SELECT count(*) FROM saved_workouts WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND detached_at IS NULL"
)
_DELETE = text("DELETE FROM classes WHERE id = :class_id AND owner_id = :owner_id")


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    """``reason`` is ``None`` when the class went; ``kept`` is the number of MOWL workouts
    classbeat made that stay in the user's account, ``playlists`` those left in Spotify and
    ``removal_job_id`` the ``player_remove`` job the delete enqueued, if any."""

    reason: str | None = None
    kept: int = 0
    playlists: tuple[song_views.PlaylistRef, ...] = ()
    removal_job_id: UUID | None = None


def _ids(ctx: UserContext, class_id: UUID) -> dict[str, Any]:
    return {"owner_id": ctx.user_id, "class_id": class_id}


async def delete(
    factory: async_sessionmaker[AsyncSession],
    ctx: UserContext,
    class_id: UUID,
    *,
    confirm_uncertain: bool,
) -> DeleteOutcome:
    """Orphaned writes are resolved first; the class row is locked (no job or operation of the
    class can be inserted meanwhile); then, in this order, ``class_busy`` (an open job),
    ``call_may_be_running`` (a save or update row ``pending`` or ``uncertain`` and inside its
    bound), ``uncertain_write`` (an ``uncertain`` row, unless confirmed). A refusal returns
    normally, so what ``resolve_orphans`` marked stays marked.
    A class sent to the player gets its ``player_remove`` job in this transaction (none while
    the player is not connected, ``player_send.publications.on_class_delete``); a class never
    sent takes no lock on the connection row. The cascade is the schema's (migrations 0004 and
    0005); ``agent_runs`` keeps its rows."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        await seams.resolve_orphans(db, ctx)
        if (await db.execute(LOCK_CLASS, _ids(ctx, class_id))).first() is None:
            return DeleteOutcome("not_found")
        if (await db.execute(OPEN_JOB_SQL, _ids(ctx, class_id))).first() is not None:
            return DeleteOutcome("class_busy")
        writes = (await db.execute(_WRITES, _ids(ctx, class_id))).all()
        if any(w.state == "pending" or w.inside_bound for w in writes):
            return DeleteOutcome("call_may_be_running")
        if writes and not confirm_uncertain:
            return DeleteOutcome("uncertain_write")
        kept = int((await db.execute(_KEPT, _ids(ctx, class_id))).scalar_one())
        left = (await song_views.playlists_of(db, ctx.user_id, class_id)).all
        job_id = await on_class_delete(db, ctx.user_id, class_id)  # ADR 0034 section 5
        await db.execute(_DELETE, _ids(ctx, class_id))
    return DeleteOutcome(None, kept, left, job_id)


def deleted_out(done: DeleteOutcome) -> DeletedOut:
    left = [PlaylistNameOut(name=p.name, detached=p.detached) for p in done.playlists]
    return DeletedOut(
        deleted=True,
        mowl_workouts_kept=done.kept,
        note=KEPT_NOTE,
        spotify_playlists_left=left,
        spotify_note=SPOTIFY_NOTE if left else None,
        player_removal_started=done.removal_job_id is not None,
        player_removal_job_id=done.removal_job_id,
    )

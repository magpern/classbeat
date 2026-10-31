"""``design_with_songs``'s checks in the jobs API's transactions (ADR 0008 sections 2, 4 and 5).

**``admit``** (``POST /api/jobs``, inside the create transaction, under the owner lock, after the
generic checks: both connections are the user's current ones, the class is theirs and has no open
job, the per-user limit), in this order, each refusal writing nothing:

1. ``song_picking_off`` unless ``CLASSBEAT_SONG_PICKING`` is on (``classbeat.songs.config``) and
   the effective ``song_picking`` flag admits the user (``classbeat.flags``: ``granted`` needs the
   ``song_picking`` feature, ``off`` refuses everyone, ``on`` admits everyone);
2. for ``mode = "resume"`` (section 6), instead of steps 3 to 6: ``not_connected`` (the Spotify
   row), then **the receipt rule**, ``receipt_invalid`` unless all hold, then ``account_changed``
   (the connected Spotify account must be the list's):

   - the named list is the class's **newest** ``song_list`` and still hashes to its stored hash;
   - a job **of the same kind** of that class was **approved** on it (``approval_revision_id`` and
     ``approved_at``) and ended ``failed``, ``cancelled`` or ``expired`` (the receipt: the user's
     click; a ``revise_songs`` list is resumed by ``revise_songs``, ``classbeat.songs.revise``);
   - **no** job of the class whose approved or resumed list it is has ``completed`` (the receipt
     is spent once its list is done) or is still ``queued`` or ``running`` (another resume in
     flight). After a resume that failed, the user may resume again against the same receipt.

   An expired, superseded or never-approved list therefore cannot be resumed. Last,
   ``write_unresolved`` while a final-playlist write of the class is ``pending`` or ``uncertain``
   (``classbeat.operations.orphans.playlist_unresolved``, after ``resolve``): a resume never makes
   another playlist before the user's Check again or Do it anyway on it. The new job reserves the
   credit again (the failed one released it);
3. ``not_connected`` unless the Spotify row is ``connected`` with a Spotify user id (share-locked
   for the rest of the transaction);
4. ``consent_missing`` unless the user's pool consent is given for that Spotify account;
5. ``pool_busy`` while the user has another pick (``design_with_songs`` or ``revise_songs`` in
   mode ``pick``) that is ``queued`` or ``running`` and not yet approved: the pool playlist is
   shared by all of a user's classes (section 2). A job ``waiting_for_approval`` no longer needs
   the pool and does not count; nor does an approved one (phase 2 never touches the pool);
6. for a ``seed`` (``invalid_input`` otherwise): the seed is the **newest** ``song_list`` revision
   of the class, made by a ``design_with_songs`` job (a song change's list is changed again by
   ``revise_songs``) that ended ``cancelled`` or ``expired`` and was never approved, its
   stored hash still matches its content (``song_list.load``), and every ``replace`` slot is a
   position in it.

**``approve_check``** (``POST /api/jobs/{id}/approve``, the ``JobKind.approve_check`` seam, after
the row checks and the connection checks of ``CONNECTIONS``; it only reads):

- ``hash_mismatch`` unless the approved revision, read again, is a ``song_list`` of the job's class
  whose content still hashes to its stored ``content_hash``, and that hash is the approved one: the
  hash binds the exact ids, texts, numbers and order;
- ``not_connected`` unless the Spotify row is ``connected`` with a Spotify user id;
- ``account_changed`` when that Spotify account is not the one the list was made for
  (``music.spotify_user_id``): a reconnect as another Spotify user keeps the row id, so the generic
  connection check alone would let it pass.

Reasons are codes only; nothing of the rows is logged or returned.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections
from classbeat.context import UserContext
from classbeat.flags import effective as flags
from classbeat.jobs.kinds import SONG_KIND_NAMES, Admitted, Approving, Refused
from classbeat.operations import orphans
from classbeat.songs import config, song_list
from classbeat.songs.inputs import DesignWithSongsInput, ReviseSongsInput, Seed

KIND: Final = "design_with_songs"

SEED_ENDED: Final = ("cancelled", "expired")

# Another pick of the user that may still write or read the pool: not yet approved, still queued
# or running. ``revise_songs`` (SJ3) stores ``mode`` the same way.
_PICK_OPEN = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND kind = ANY(:kinds) "
    "AND state IN ('queued', 'running') AND approved_at IS NULL "
    "AND coalesce(input ->> 'mode', 'pick') = 'pick' LIMIT 1"
)
# The class's newest song list and the job that made it.
_NEWEST_LIST = text(
    "SELECT r.id, j.kind, j.state, j.approved_at FROM class_revisions r "
    "LEFT JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'song_list' "
    "ORDER BY r.number DESC LIMIT 1"
)


# Jobs of the class that approved or resumed the list ``revision_id``, by state; ``approved`` only
# for a job of the resuming kind (a list is resumed by the kind that made it), newest first.
_FOR_LIST = text(
    "SELECT state, input, approved_at IS NOT NULL AND approval_revision_id = :revision_id "
    "AND kind = :kind AS approved "
    "FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id AND ("
    "(approval_revision_id = :revision_id AND approved_at IS NOT NULL) OR "
    "(kind = ANY(:kinds) AND input -> 'resume' ->> 'song_list_revision_id' = :revision_text)) "
    "ORDER BY approved_at DESC NULLS LAST"
)
RECEIPT_ENDED: Final = ("failed", "cancelled", "expired")


@dataclass(frozen=True)
class Receipt:
    """A valid receipt: the approved list and the input of the job that approved it."""

    stored: song_list.SongListRevision
    approved_input: dict[str, Any]


async def receipt_valid(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID, kind: str = KIND
) -> Receipt | None:
    """The list a resume of ``kind`` may start phase 2 for (module docstring, step 2), or None."""
    newest = (await db.execute(_NEWEST_LIST, {"owner_id": owner_id, "class_id": class_id})).first()
    if newest is None or newest.id != revision_id:
        return None
    rows = (
        await db.execute(
            _FOR_LIST,
            {
                "owner_id": owner_id,
                "class_id": class_id,
                "revision_id": revision_id,
                "revision_text": str(revision_id),
                "kinds": list(SONG_KIND_NAMES),
                "kind": kind,
            },
        )
    ).all()
    if any(r.state in ("completed", "queued", "running") for r in rows):
        return None  # spent, or another resume (or the approved phase itself) in flight
    approved = next((r for r in rows if r.approved and r.state in RECEIPT_ENDED), None)
    if approved is None or not isinstance(approved.input, dict):
        return None  # never approved (by this kind)
    stored = await song_list.load(db, owner_id, class_id, revision_id)
    if stored is None:
        return None
    return Receipt(stored, approved.input)  # pyright: ignore[reportUnknownArgumentType]


async def _admit_resume(
    db: AsyncSession, ctx: UserContext, parsed: DesignWithSongsInput
) -> Admitted | Refused:
    if parsed.resume is None or parsed.class_id is None:  # the input model refuses both
        return Refused("receipt_invalid")
    account = await connections.spotify_account_in(db, ctx, parsed.spotify_connection_id)
    if account is None:
        return Refused("not_connected")
    found = await receipt_valid(
        db, ctx.user_id, parsed.class_id, parsed.resume.song_list_revision_id
    )
    if found is None:
        return Refused("receipt_invalid")
    if found.stored.account != account:
        return Refused("account_changed")
    if await orphans.playlist_unresolved(db, ctx, parsed.class_id):
        return Refused("write_unresolved")  # the user decides the earlier write first
    return Admitted()


async def pool_busy(db: AsyncSession, owner_id: UUID) -> bool:
    found = await db.execute(_PICK_OPEN, {"owner_id": owner_id, "kinds": list(SONG_KIND_NAMES)})
    return found.first() is not None


async def seed_list(
    db: AsyncSession, owner_id: UUID, class_id: UUID, seed: Seed, *, newest: bool = True
) -> song_list.SongListRevision | None:
    """The seed's list when it may seed a pick (module docstring, step 6); None otherwise.
    ``newest=False`` (the worker, after ``admit`` accepted) skips the newest-and-ended rule, which
    the job's own wait would change, and re-checks the rest."""
    if newest:
        row = (await db.execute(_NEWEST_LIST, {"owner_id": owner_id, "class_id": class_id})).first()
        if (
            row is None
            or row.id != seed.song_list_revision_id
            or row.kind != KIND  # a song change's list is changed again by revise_songs
            or row.state not in SEED_ENDED
            or row.approved_at is not None
        ):
            return None
    found = await song_list.load(db, owner_id, class_id, seed.song_list_revision_id)
    if found is None or any(slot >= len(found.content.songs) for slot in seed.replace):
        return None
    return found


async def pick_checks(
    db: AsyncSession, ctx: UserContext, spotify_connection_id: UUID
) -> Refused | None:
    """Steps 3 to 5 (both kinds' picks): ``not_connected``, ``consent_missing``, ``pool_busy``."""
    account = await connections.spotify_account_in(db, ctx, spotify_connection_id)
    if account is None:
        return Refused("not_connected")
    if not await connections.pool_consent_in(db, ctx, account):
        return Refused("consent_missing")
    if await pool_busy(db, ctx.user_id):
        return Refused("pool_busy")
    return None


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, DesignWithSongsInput):
        return Refused("invalid_input")
    if not await flags.song_picking_admits(db, ctx, config.current().enabled):
        return Refused("song_picking_off")
    if parsed.mode == "resume":
        return await _admit_resume(db, ctx, parsed)
    refused = await pick_checks(db, ctx, parsed.spotify_connection_id)
    if refused is not None:
        return refused
    if parsed.seed is not None:
        if parsed.class_id is None:  # the input model refuses it already
            return Refused("invalid_input")
        if await seed_list(db, ctx.user_id, parsed.class_id, parsed.seed) is None:
            return Refused("invalid_input")
    return Admitted()


async def approve_check(db: AsyncSession, ctx: UserContext, approving: Approving, /) -> str | None:
    given = approving.input
    if not isinstance(given, DesignWithSongsInput | ReviseSongsInput):
        return "invalid_input"
    stored = await song_list.load(db, ctx.user_id, approving.class_id, approving.revision_id)
    if stored is None or stored.content_hash != approving.content_hash:
        return "hash_mismatch"
    account = await connections.spotify_account_in(db, ctx, given.spotify_connection_id)
    if account is None:
        return "not_connected"
    if account != stored.account:
        return "account_changed"
    return None

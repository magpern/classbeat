"""``studio_songs``' checks in the jobs API's transactions (ADR 0036 sections 2 and 3; ADR 0008
section 5).

**``admit``** (``POST /api/jobs`` and the studio's routes, inside the create transaction, under
the owner lock, after the generic checks), in this order, each refusal writing nothing:

1. ``feature_off`` unless the studio runs (``studio_on``: ``design`` in
   ``CLASSBEAT_SWEDEBEAT_STUDIO``, the effective ``swedebeat_enabled`` flag, the gateway's URL and
   key) and the user's own "I use Swedebeat" preference is on; then ``song_picking_off`` unless
   song picking admits the user (the studio picks songs from a Spotify search);
2. for a class: ``invalid_input`` for a soundless class, for a class whose music is of another
   kind (a stored ``music_source`` or a ``song_list`` that is not the studio's: a class's music
   kind never changes), and for a brief of another sport;
3. ``not_connected`` unless the SwedeBeat row is ``connected`` and not past its ``expires_at``;
4. the song pick's steps (``songs.admit.pick_checks``): ``not_connected`` (Spotify),
   ``consent_missing``, ``pool_busy`` (every action writes the pool, the working copy);
5. for an action on a list (``invalid_input`` otherwise): the seed is the class's **newest**
   ``song_list``, made by a ``studio_songs`` job that ended ``cancelled`` or ``expired`` and was
   never approved, it still hashes to its stored hash (``song_list.load``), and the action fits
   it: ``swap_anyway`` a ``suspected_unavailable`` list, ``undo`` a swap not yet undone at that
   position, ``another`` a swapped slot or a gap.

**``approve_check``** (the ``JobKind.approve_check`` seam; it only reads):
``hash_mismatch`` unless the list, read again, hashes to the approved hash; ``list_has_gap`` for a
list with a gap or a suspected one (a gap is never approved into a mix,
``swap_policy.mix_tracks``); ``not_connected`` and ``account_changed`` for the Spotify account the
list was made for; ``connection_changed`` when the job's SwedeBeat connection is not the list's;
the current plan's gates for a job without any (as ``songs.admit``).

Reasons are codes only; nothing of the rows is logged or returned.
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections
from classbeat.connections import find_in
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.connections.swedebeat_account import live
from classbeat.context import UserContext
from classbeat.design import music_kind, sport
from classbeat.flags import effective as flags
from classbeat.jobs.kinds import Admitted, Approving, Refused
from classbeat.quota.gates import brief_of, check_brief, current_gates
from classbeat.songs import config as song_config
from classbeat.songs.admit import pick_checks
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_studio import config, song_list
from classbeat.swedebeat_studio.inputs import StudioSongsInput
from classbeat.swedebeat_studio.song_list_model import SOURCE
from classbeat.swedebeat_studio.swap_policy import GapInMix, askable, mix_tracks, undoable

KIND: Final = "studio_songs"
PROVIDER: Final = "swedebeat"
SEED_ENDED: Final = ("cancelled", "expired")

# The class's stored music source and its newest song list's source and job.
_CLASS_MUSIC = text(
    "SELECT c.music_source ->> 'source' AS stored, r.id AS list_id, "
    "r.music ->> 'source' AS list_source, j.kind, j.state, j.approved_at "
    "FROM classes c LEFT JOIN LATERAL (SELECT id, music, job_id FROM class_revisions "
    "WHERE owner_id = c.owner_id AND class_id = c.id AND kind = 'song_list' "
    "ORDER BY number DESC LIMIT 1) r ON true "
    "LEFT JOIN jobs j ON j.owner_id = c.owner_id AND j.id = r.job_id "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)


async def studio_on(db: AsyncSession) -> bool:
    """The studio's song stage runs here: ``design``, the effective SwedeBeat flag, a gateway."""
    match = match_config.current()
    if not config.current().design or not match.available:
        return False
    return await flags.swedebeat_enabled(db, match.enabled)


async def _swedebeat_connected(db: AsyncSession, ctx: UserContext, connection_id: UUID) -> bool:
    if ctx.connection(PROVIDER) != connection_id:
        return False
    info = await find_in(db, ctx, connection_id)
    if info is None or info.provider != PROVIDER or info.status != "connected":
        return False
    return live(info.expires_at, datetime.now(UTC))  # ADR 0035 section 5: never a doomed job


async def _class_checks(
    db: AsyncSession, ctx: UserContext, parsed: StudioSongsInput
) -> Refused | None:
    fixed = await sport.admit_brief(db, ctx, parsed.brief, parsed.class_id)
    if fixed is not None or parsed.class_id is None:
        return fixed
    other = await music_kind.admit_studio(db, ctx.user_id, parsed.class_id)
    if other is not None:  # soundless or another music kind: a class's kind never changes
        return other
    row = (
        await db.execute(_CLASS_MUSIC, {"owner_id": ctx.user_id, "class_id": parsed.class_id})
    ).first()
    if row is None:
        return Refused("class_not_found")
    if row.stored not in (None, SOURCE) or row.list_source not in (None, SOURCE):
        return Refused("invalid_input")  # a class's music kind never changes
    if parsed.action == "pick":
        return None
    seed = parsed.the_seed
    if (
        row.list_id != seed.song_list_revision_id
        or row.kind != KIND
        or row.state not in SEED_ENDED
        or row.approved_at is not None
    ):
        return Refused("invalid_input")
    return await _seed_fits(db, ctx, parsed)


async def _seed_fits(
    db: AsyncSession, ctx: UserContext, parsed: StudioSongsInput
) -> Refused | None:
    seed, class_id = parsed.the_seed, parsed.class_id
    assert class_id is not None  # noqa: S101 - every action but pick names its class
    stored = await song_list.load(db, ctx.user_id, class_id, seed.song_list_revision_id)
    if stored is None:
        return Refused("invalid_input")
    content = stored.content
    position = seed.position if seed.position is not None else -1
    fits = {
        "swap_anyway": content.evidence.state == "suspected_unavailable",
        "undo": undoable(content, position),
        "another": askable(content, position),
    }
    return None if fits.get(parsed.action, False) else Refused("invalid_input")


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, StudioSongsInput):
        return Refused("invalid_input")
    if not await studio_on(db) or not await uses_swedebeat_in(db, ctx.user_id):
        return Refused("feature_off")
    if not await flags.song_picking_admits(db, ctx, song_config.current().enabled):
        return Refused("song_picking_off")
    refused = await _class_checks(db, ctx, parsed)
    if refused is not None:
        return refused
    if not await _swedebeat_connected(db, ctx, parsed.swedebeat_connection_id):
        return Refused("not_connected")
    refused = await pick_checks(db, ctx, parsed.spotify_connection_id)
    return refused if refused is not None else Admitted()


async def approve_check(db: AsyncSession, ctx: UserContext, approving: Approving, /) -> str | None:
    given = approving.input
    if not isinstance(given, StudioSongsInput):
        return "invalid_input"
    stored = await song_list.load(db, ctx.user_id, approving.class_id, approving.revision_id)
    if stored is None or stored.content_hash != approving.content_hash:
        return "hash_mismatch"
    try:
        mix_tracks(stored.content)
    except GapInMix:
        return "list_has_gap"
    account = await connections.spotify_account_in(db, ctx, given.spotify_connection_id)
    if account is None:
        return "not_connected"
    if account != stored.music.spotify_user_id:
        return "account_changed"
    if str(given.swedebeat_connection_id) != stored.music.swedebeat_connection_id:
        return "connection_changed"
    if given.plan_gates is None:  # fail closed (#496): the current plan, QuotaRefused is 409
        plan, gates = await current_gates(db, ctx)
        check_brief(gates, await brief_of(db, ctx, given), plan.id)
    return None

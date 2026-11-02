"""The gate of a design on the user's Swedebeat mix (ADR 0016 sections 2, 3 and 7; A4).

``admit_mix`` runs in ``design_draft``'s ``admit`` (the jobs API's create transaction, under the
owner lock) for the music source ``mowl_swedebeat_mix``, in this order, each refusal writing
nothing:

1. ``feature_off`` unless the effective ``swedebeat_enabled`` flag is on (the environment's
   switch, ``MixAdmission.enabled``, then the stored flag; stricter wins). Nothing else is read.
2. ``not_connected`` unless the job's ``mowl_connection_id`` is the user's MOWL connection and
   ``connected``.
3. ``invalid_input`` unless ``mix_id`` is in the user's **own fresh listing** (``POST
   v1/swedebeat/Playlists`` through MOWL with the user's MOWL token, ``parse_mixes``): another
   user's id, a made-up id or a URL never becomes a job. A failed listing refuses with its
   connection category. The listing has a total deadline of ``LISTING_SECONDS``, well inside the
   role's ``idle_in_transaction_session_timeout``.
4. ``analysis_limit`` (with ``limit`` and ``used``) when the mix has no usable cached analysis and
   the user started ``mixlimit.ANALYSES_PER_DAY`` new analyses in the last 24 hours.

The worker repeats what can change after creation: ``still_on`` (a queued job whose flag went off
ends ``feature_off``, calling nothing), and the listing and ``mixlimit.reserve`` inside
``swedebeat_music`` before anything is attached or fetched. ``revise_draft`` on a class designed
on a mix checks the flag and the limit (``admit_class_mix``); its mix id is the class's own.

The web app sets ``MixAdmission`` at start (``classbeat.web``: the switch and the listing, which
opens the user's MOWL connection through ``classbeat.connections``). Without it the switch is off
and every design on a mix is refused ``feature_off``.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Final, Protocol
from uuid import UUID

from mowl import api as mowl_api
from mowl import music
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import connections, egress
from classbeat.connections import ConnectionFailure, Credentials, call_opened, open_connection
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.design import mixlimit
from classbeat.design.swedebeat_music import MAP_TTL_DAYS
from classbeat.egress import OutboundBlocked
from classbeat.flags import effective
from classbeat.jobs import JobFailure, JobRun
from classbeat.jobs.kinds import Refused
from classbeat.operations.swedebeat import parse_mixes

LISTING_SECONDS: Final = 20.0
MIX_SOURCE: Final = "mowl_swedebeat_mix"


class Listing(Protocol):
    """The user's fresh Swedebeat listing as MOWL answers it (annotations only)."""

    def __call__(self, ctx: UserContext, /) -> Awaitable[object]: ...


# A cache hit as ``swedebeat_music._cached`` judges it: young enough, this connection, the stored
# link's playlist and the library's current map version. The user's own rows only (RLS).
_CACHED = text(
    "SELECT 1 FROM swedebeat_mix_maps m JOIN mowl_playlist_links l ON l.owner_id = m.owner_id "
    "AND l.source = 'swedebeat' AND l.source_id = m.mix_id AND l.playlist_id = m.playlist_id "
    "WHERE m.owner_id = :owner_id AND m.mix_id = :mix_id AND m.mowl_connection_id = :connection_id "
    "AND m.created_at > now() - make_interval(days => :days) AND m.map->>'version' = :version"
)
_CLASS_MIX = text(
    "SELECT music_source->>'mix_id' AS mix_id FROM classes "
    "WHERE owner_id = :owner_id AND id = :class_id AND music_source->>'source' = :source"
)


@dataclass(frozen=True, slots=True)
class MixAdmission:
    enabled: bool = False  # CLASSBEAT_SWEDEBEAT_ENABLED
    listing: Listing | None = None  # the user's fresh listing (web only)


_config = MixAdmission()


def configure(config: MixAdmission | None) -> None:
    """Set the web app's admission (None: off)."""
    global _config
    _config = config or MixAdmission()


def current() -> MixAdmission:
    return _config


def _list_mixes(creds: Credentials) -> Any:
    with egress.call_deadline(LISTING_SECONDS):
        return mowl_api.swedebeat_mixes(creds)


def web_listing(factory: async_sessionmaker[AsyncSession], keyring: KeyRing) -> Listing:
    """The listing ``admit`` reads: one MOWL request with the user's own MOWL connection."""

    async def listing(ctx: UserContext) -> object:
        opened = await open_connection(factory, keyring, ctx, "mowl")
        return await call_opened(factory, ctx, opened, _list_mixes)

    return listing


async def flag_on(db: AsyncSession, environment_on: bool) -> bool:
    return await effective.swedebeat_enabled(db, environment_on)


async def _cached(db: AsyncSession, owner_id: UUID, mix_id: str, connection_id: UUID) -> bool:
    params = {
        "owner_id": owner_id,
        "mix_id": mix_id,
        "connection_id": connection_id,
        "days": MAP_TTL_DAYS,
        "version": str(music.MAP_VERSION),
    }
    return (await db.execute(_CACHED, params)).first() is not None


async def _limit(
    db: AsyncSession, owner_id: UUID, mix_id: str, connection_id: UUID
) -> Refused | None:
    if await _cached(db, owner_id, mix_id, connection_id):
        return None  # a cache hit costs no slot
    used = await mixlimit.used(db, owner_id)
    if used >= mixlimit.ANALYSES_PER_DAY:
        return Refused(mixlimit.CATEGORY, {"limit": mixlimit.ANALYSES_PER_DAY, "used": used})
    return None


async def _connected(db: AsyncSession, ctx: UserContext, connection_id: UUID) -> bool:
    if ctx.connection("mowl") != connection_id:
        return False
    info = await connections.find_in(db, ctx, connection_id)
    return info is not None and info.provider == "mowl" and info.status == "connected"


async def admit_mix(
    db: AsyncSession, ctx: UserContext, mix_id: str, connection_id: UUID
) -> Refused | None:
    """The module docstring's checks for a new design; None admits."""
    config = current()
    if not await flag_on(db, config.enabled) or config.listing is None:
        return Refused("feature_off")
    if not await _connected(db, ctx, connection_id):
        return Refused("not_connected")
    try:
        answer = await config.listing(ctx)
    except ConnectionFailure as failure:
        return Refused(failure.category)
    except OutboundBlocked:  # the host only is known; nothing of it goes back
        return Refused("outbound_blocked")
    if all(m.mix_id != mix_id for m in parse_mixes(answer)):
        return Refused("invalid_input")
    return await _limit(db, ctx.user_id, mix_id, connection_id)


async def admit_class_mix(
    db: AsyncSession, ctx: UserContext, class_id: UUID, connection_id: UUID
) -> Refused | None:
    """``revise_draft``: for a class designed on a mix, the flag and the limit; any other class
    passes untouched (nothing Swedebeat is read beyond its ``music_source``)."""
    row = (
        await db.execute(
            _CLASS_MIX, {"owner_id": ctx.user_id, "class_id": class_id, "source": MIX_SOURCE}
        )
    ).first()
    if row is None:
        return None
    if not await flag_on(db, current().enabled):
        return Refused("feature_off")
    if not isinstance(row.mix_id, str):
        return Refused("invalid_input")
    return await _limit(db, ctx.user_id, row.mix_id, connection_id)


async def still_on(run: JobRun, environment_on: bool) -> None:
    """In the worker, before the music step of a job on a mix: ``feature_off`` when the flag is
    off now (the job was queued while it was on), before anything is called."""
    async with run.fenced() as fs:
        on = await flag_on(fs.db, environment_on)
    if not on:
        raise JobFailure("feature_off")

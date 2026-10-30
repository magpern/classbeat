"""The user's Spotify connection as the account page sees it, and the standing pool consent
(ADR 0009 sections 2 and 9).

- ``spotify_status``: the row's status, ``last_error`` and the user's own Client ID, and whether
  the pool consent is given. Never a token, the Spotify user id or a display name.
- ``give_pool_consent``: needs a ``connected`` row (its lock shared while the consent row is
  written, so a parallel disconnect or account change cannot leave a consent for another
  account); inserts ``spotify_pool`` with ``playlist_id NULL`` and the connection's Spotify user
  id. Giving it again only refreshes ``consent_version`` and ``consented_at``.
- ``spotify_account_in``, ``pool_consent_in``: inside the caller's transaction (the jobs API's
  create and approve, ADR 0008 sections 4 and 5): the Spotify user id of the user's ``connected``
  Spotify row, and whether the pool consent is given for that account. Neither returns more.
- ``revoke_pool_consent``: in **one transaction**, after the connection row lock, the consent row
  and every pool operation row (the succeeded create that holds the old playlist id and the
  replace rows whose hash covers it included) are deleted (``spotify_data.revoke_pool``), so the
  old playlist id is never touched again.
"""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.spotify_data import revoke_pool
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.db import user_session

PROVIDER = "spotify"
Factory = async_sessionmaker[AsyncSession]
_C = connections.c

_POOL = text("SELECT consent_version FROM spotify_pool WHERE owner_id = :owner_id")
_GIVE = text(
    "INSERT INTO spotify_pool (owner_id, spotify_user_id, consent_version) "
    "VALUES (:owner_id, :spotify_user_id, :version) "
    "ON CONFLICT (owner_id) DO UPDATE SET consent_version = EXCLUDED.consent_version, "
    "consented_at = now(), updated_at = now() "
    "WHERE spotify_pool.spotify_user_id = EXCLUDED.spotify_user_id "
    "RETURNING id"
)
_EXISTS = text("SELECT 1 FROM spotify_pool WHERE owner_id = :owner_id")


@dataclass(frozen=True, slots=True)
class SpotifyStatus:
    status: str  # not_connected, connected, needs_reconnect
    last_error: str | None
    client_id: str | None
    pool_consent: bool
    consent_version: int | None


async def spotify_status(factory: Factory, ctx: UserContext) -> SpotifyStatus:
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                select(_C.status, _C.last_error, _C.public).where(
                    _C.owner_id == ctx.user_id, _C.provider == PROVIDER
                )
            )
        ).first()
        version = (await db.execute(_POOL, {"owner_id": ctx.user_id})).scalar_one_or_none()
    if row is None:
        return SpotifyStatus("not_connected", None, None, version is not None, version)
    client_id = (row.public or {}).get("client_id")
    return SpotifyStatus(
        str(row.status),
        row.last_error,
        client_id if isinstance(client_id, str) else None,
        version is not None,
        version,
    )


async def give_pool_consent(factory: Factory, ctx: UserContext, version: int) -> bool:
    """False (nothing written) when the user has no ``connected`` Spotify row."""
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                select(_C.status, _C.public)
                .where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER)
                .with_for_update(read=True)
            )
        ).first()
        if row is None or row.status != "connected":
            return False
        spotify_user_id = (row.public or {}).get("spotify_user_id")
        if not isinstance(spotify_user_id, str) or not spotify_user_id:
            return False
        await db.execute(
            _GIVE,
            {"owner_id": ctx.user_id, "spotify_user_id": spotify_user_id, "version": version},
        )
        # A row of another Spotify user id cannot exist (an account change deletes it, under
        # this same row lock); were it there, the conflict update above leaves it untouched.
        return (await db.execute(_EXISTS, {"owner_id": ctx.user_id})).first() is not None


_CONSENT_FOR = text(
    "SELECT 1 FROM spotify_pool WHERE owner_id = :owner_id AND spotify_user_id = :spotify_user_id"
)


async def spotify_account_in(
    db: AsyncSession, ctx: UserContext, connection_id: object
) -> str | None:
    """The Spotify user id of the user's ``connected`` Spotify row ``connection_id`` (share-locked
    until the caller's transaction ends, so a reconnect cannot pass in between), or None."""
    row = (
        await db.execute(
            select(_C.status, _C.public)
            .where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER, _C.id == connection_id)
            .with_for_update(read=True)
        )
    ).first()
    if row is None or row.status != "connected":
        return None
    account = (row.public or {}).get("spotify_user_id")
    return account if isinstance(account, str) and account else None


async def pool_consent_in(db: AsyncSession, ctx: UserContext, account: str) -> bool:
    """Whether the user's pool consent is given, for the Spotify account ``account``."""
    found = await db.execute(_CONSENT_FOR, {"owner_id": ctx.user_id, "spotify_user_id": account})
    return found.first() is not None


async def revoke_pool_consent(factory: Factory, ctx: UserContext) -> bool:
    """True when a consent row was there."""
    async with user_session(factory, ctx) as db:
        # The connection row lock first, as every Spotify forget path takes it (spotify_data).
        await db.execute(
            select(_C.id)
            .where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER)
            .with_for_update()
        )
        existed = (await db.execute(_EXISTS, {"owner_id": ctx.user_id})).first() is not None
        await revoke_pool(db, ctx.user_id)
        return existed

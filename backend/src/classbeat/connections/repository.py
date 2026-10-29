"""The connections repository (ADR 0004 §1, §3, §4): the only code that turns rows into
credentials.

Every function takes a ``UserContext`` and works in ``user_session`` (RLS as ``ctx.user_id``)
and filters on ``owner_id = ctx.user_id`` itself as well. An id of another user's row is not
found, the same as an unknown one. Writes:

- **connect and reconnect** (``save``): one transaction. ``SELECT ... FOR UPDATE`` on
  ``(owner_id, provider)``; keep the row's ``id`` or make a new one in the application; encrypt
  under a new DEK with that ``id`` in the AAD; ``UPDATE`` or ``INSERT``. An ``INSERT`` that hits
  ``UNIQUE (owner_id, provider)`` (a parallel connect) rolls back and is retried once from the
  start in a new transaction. Never ``INSERT ... ON CONFLICT DO UPDATE`` (it would keep the old
  ``id`` with a payload encrypted for a new one).
  A MOWL reconnect also forgets the user's MOWL playlist links and attach operations in the
  same transaction, as a disconnect does (the token may belong to another MOWL account).
- **status changes** (``needs_reconnect``, ``last_error``, ``last_used_at``) are conditional on
  the version that was read: ``WHERE id = :id AND wrapped_dek = :read_wrapped_dek``. They never
  touch ``secret``, ``wrapped_dek`` or ``key_id``.
"""

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from mowl.errors import MowlError
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from classbeat.connections.credentials import (
    PAYLOAD_FIELDS,
    Credentials,
    OpenConnection,
    build_credentials,
)
from classbeat.connections.errors import ConnectionFailure, run_library_call
from classbeat.connections.tables import PROVIDERS, connections
from classbeat.context import UserContext
from classbeat.crypto.envelope import (
    DecryptFailed,
    canonical_json,
    connection_aad,
    connection_wrap_aad,
    open_sealed,
    seal,
)
from classbeat.crypto.keks import KeyRing, KeyUnavailable
from classbeat.db import user_session

logger = logging.getLogger(__name__)

PROVIDER_UNIQUE = "connections_owner_id_provider_key"
Factory = async_sessionmaker[AsyncSession]
_C = connections.c


def _check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    return provider


def _is_provider_conflict(error: IntegrityError) -> bool:
    orig = error.orig
    diag = getattr(orig, "diag", None)
    return (
        getattr(orig, "sqlstate", None) == "23505"
        and getattr(diag, "constraint_name", None) == PROVIDER_UNIQUE
    )


async def connection_refs(factory: Factory, ctx: UserContext) -> frozenset[tuple[str, UUID]]:
    """The ``(provider, id)`` pairs of the user's rows, for ``UserContext.connections``."""
    async with user_session(factory, ctx) as db:
        return await refs_in(db, ctx)


async def refs_in(db: AsyncSession, ctx: UserContext) -> frozenset[tuple[str, UUID]]:
    rows = await db.execute(select(_C.provider, _C.id).where(_C.owner_id == ctx.user_id))
    return frozenset((row.provider, row.id) for row in rows)


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    """What the account page shows: never a secret, never ``public``."""

    id: UUID
    provider: str
    status: str
    last_error: str | None
    connected_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


_INFO = (
    _C.id,
    _C.provider,
    _C.status,
    _C.last_error,
    _C.connected_at,
    _C.last_used_at,
    _C.expires_at,
)


async def list_connections(factory: Factory, ctx: UserContext) -> list[ConnectionInfo]:
    async with user_session(factory, ctx) as db:
        rows = await db.execute(
            select(*_INFO).where(_C.owner_id == ctx.user_id).order_by(_C.provider)
        )
        return [ConnectionInfo(*row) for row in rows]


async def find(factory: Factory, ctx: UserContext, connection_id: UUID) -> ConnectionInfo | None:
    """The user's connection with this id; another user's id is not found (a 404)."""
    async with user_session(factory, ctx) as db:
        return await find_in(db, ctx, connection_id)


async def find_in(db: AsyncSession, ctx: UserContext, connection_id: UUID) -> ConnectionInfo | None:
    """``find`` inside the caller's ``user_session`` (the jobs API's approve transaction)."""
    row = (
        await db.execute(select(*_INFO).where(_C.id == connection_id, _C.owner_id == ctx.user_id))
    ).first()
    return None if row is None else ConnectionInfo(*row)


async def save(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    provider: str,
    secret: Mapping[str, Any],
    *,
    public: Mapping[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> UUID:
    """Connect or reconnect: store ``secret`` (the provider's payload fields) encrypted.
    Returns the row id (kept across reconnects)."""
    _check_provider(provider)
    if set(secret) != PAYLOAD_FIELDS[provider]:
        raise ValueError("the payload does not have the provider's fields")
    plaintext = canonical_json(dict(secret))
    public_values = dict(public or {})
    try:
        async with user_session(factory, ctx) as db:
            return await _write(db, ring, ctx, provider, plaintext, public_values, expires_at)
    except IntegrityError as error:
        if not _is_provider_conflict(error):
            raise
    # A parallel connect inserted the row first: start again in a new transaction (once).
    logger.info("parallel connect; retrying once", extra={"provider": provider})
    async with user_session(factory, ctx) as db:
        return await _write(db, ring, ctx, provider, plaintext, public_values, expires_at)


async def _write(
    db: AsyncSession,
    ring: KeyRing,
    ctx: UserContext,
    provider: str,
    plaintext: bytes,
    public: dict[str, Any],
    expires_at: datetime | None,
) -> UUID:
    existing = (
        await db.execute(
            select(_C.id)
            .where(_C.owner_id == ctx.user_id, _C.provider == provider)
            .with_for_update()
        )
    ).scalar_one_or_none()
    row_id = existing if existing is not None else uuid.uuid4()
    kek = await ring.current(db)
    sealed = seal(
        plaintext,
        connection_aad(ctx.user_id, provider, row_id),
        kek,
        connection_wrap_aad(kek.key_id, ctx.user_id, provider, row_id),
    )
    values: dict[str, Any] = {
        "status": "connected",
        "last_error": None,
        "public": public,
        "secret": sealed.ciphertext,
        "secret_nonce": sealed.nonce,
        "wrapped_dek": sealed.wrapped_dek,
        "dek_nonce": sealed.dek_nonce,
        "key_id": sealed.key_id,
        "expires_at": expires_at,
        "connected_at": func.now(),
        "updated_at": func.now(),
    }
    if existing is not None:
        if provider == "mowl":
            # A reconnect in place may be another MOWL account, and the row records no account
            # identity: it forgets the old account's links and attach operations exactly as a
            # disconnect then connect would (ADR 0006 section 3), after the row lock above.
            await _forget_mowl_account(db, ctx)
        await db.execute(
            update(connections).where(_C.id == row_id, _C.owner_id == ctx.user_id).values(**values)
        )
    else:
        await db.execute(
            insert(connections).values(id=row_id, owner_id=ctx.user_id, provider=provider, **values)
        )
    return row_id


async def open_connection(
    factory: Factory, ring: KeyRing, ctx: UserContext, provider: str
) -> OpenConnection:
    """Decrypt the user's connection for ``provider``.

    ``ConnectionFailure``: ``not_connected`` (no row, or ``needs_reconnect``), ``key_unavailable``
    (the row is not changed: an operator error) or ``decrypt_failed`` (the row flips to
    ``needs_reconnect``, conditional on the version read). Only the category and the row id are
    logged; no other key is tried.
    """
    _check_provider(provider)
    failure: str | None = None
    row_id: UUID | None = None
    opened: OpenConnection | None = None
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                select(
                    _C.id,
                    _C.status,
                    _C.public,
                    _C.secret,
                    _C.secret_nonce,
                    _C.wrapped_dek,
                    _C.dek_nonce,
                    _C.key_id,
                ).where(_C.owner_id == ctx.user_id, _C.provider == provider)
            )
        ).first()
        if row is None or row.status != "connected":
            failure = "not_connected"
        else:
            row_id = row.id
            failure, plaintext = await _decrypt(db, ring, ctx, provider, row)
            if failure == "decrypt_failed":
                await _flip(db, ctx, row.id, bytes(row.wrapped_dek), "decrypt_failed")
            elif plaintext is not None:
                failure, opened = _parse(provider, row, plaintext)
    if failure is not None or opened is None:
        category = failure or "unknown"
        if row_id is not None:
            logger.warning(
                "connection unusable", extra={"category": category, "connection_id": str(row_id)}
            )
        raise ConnectionFailure(category)
    return opened


async def _decrypt(
    db: AsyncSession, ring: KeyRing, ctx: UserContext, provider: str, row: Any
) -> tuple[str | None, bytes | None]:
    try:
        kek = await ring.kek_for(db, row.key_id)
    except KeyUnavailable:
        return "key_unavailable", None
    try:
        plaintext = open_sealed(
            row.secret,
            row.secret_nonce,
            row.wrapped_dek,
            row.dek_nonce,
            kek,
            connection_aad(ctx.user_id, provider, row.id),
            connection_wrap_aad(kek.key_id, ctx.user_id, provider, row.id),
        )
    except DecryptFailed:
        return "decrypt_failed", None
    return None, plaintext


def _parse(provider: str, row: Any, plaintext: bytes) -> tuple[str | None, OpenConnection | None]:
    try:
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            return "unknown", None
        credentials = build_credentials(provider, payload, dict(row.public or {}))  # pyright: ignore[reportUnknownArgumentType]
    except (ValueError, TypeError, KeyError):
        return "unknown", None
    return None, OpenConnection(row.id, provider, credentials, bytes(row.wrapped_dek))


async def _flip(
    db: AsyncSession, ctx: UserContext, row_id: UUID, version: bytes, category: str
) -> bool:
    result = await db.execute(
        update(connections)
        .where(_C.id == row_id, _C.owner_id == ctx.user_id, _C.wrapped_dek == version)
        .values(status="needs_reconnect", last_error=category, updated_at=func.now())
        .returning(_C.id)
    )
    return result.first() is not None


async def mark_needs_reconnect(
    factory: Factory, ctx: UserContext, opened: OpenConnection, category: str = "needs_reconnect"
) -> bool:
    """Flip to ``needs_reconnect``, only if the row still holds the version that was read (a
    stale 401 from before a reconnect leaves the fresh connection alone)."""
    async with user_session(factory, ctx) as db:
        return await _flip(db, ctx, opened.id, opened.version, category)


async def record_result(
    factory: Factory, ctx: UserContext, opened: OpenConnection, category: str | None
) -> bool:
    """After a call: ``last_used_at`` and ``last_error`` (``None`` on success), conditional on
    the version read."""
    values: dict[str, Any] = {"last_error": category}
    if category is None:
        values["last_used_at"] = func.now()
    async with user_session(factory, ctx) as db:
        result = await db.execute(
            update(connections)
            .where(_C.id == opened.id, _C.owner_id == ctx.user_id, _C.wrapped_dek == opened.version)
            .values(**values)
            .returning(_C.id)
        )
        return result.first() is not None


async def use[T](
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    provider: str,
    call: Callable[[Credentials], T],
    *,
    library_error: type[BaseException] = MowlError,
) -> T:
    """Run one library call with the user's credentials (in a worker thread). A 401 or 403
    flips the connection to ``needs_reconnect``; any failure is a ``ConnectionFailure``."""
    opened = await open_connection(factory, ring, ctx, provider)
    return await call_opened(factory, ctx, opened, call, library_error=library_error)


async def call_opened[T](
    factory: Factory,
    ctx: UserContext,
    opened: OpenConnection,
    call: Callable[[Credentials], T],
    *,
    library_error: type[BaseException] = MowlError,
    single_request: bool = False,
) -> T:
    """``use`` with a connection the caller opened already (an external operation opens it
    before writing its ``pending`` row, so ``not_connected`` costs no row, ADR 0005 section 7).
    Never inside another transaction."""
    failure: ConnectionFailure | None = None
    try:
        result = await run_library_call(
            lambda: call(opened.credentials),
            library_error=library_error,
            sign_in=False,
            single_request=single_request,
        )
    except ConnectionFailure as error:
        failure = error
    else:
        await record_result(factory, ctx, opened, None)
        return result
    # Outside the except block, so nothing new is chained onto the failure.
    if failure.category == "needs_reconnect":
        await mark_needs_reconnect(factory, ctx, opened)
    else:
        await record_result(factory, ctx, opened, failure.category)
    raise failure


async def connect(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    provider: str,
    sign_in: Callable[[], Mapping[str, Any]],
    *,
    library_error: type[BaseException] = MowlError,
    public: Mapping[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> UUID:
    """The provider's sign-in first (outside any transaction, so no row lock is held across a
    network call), then ``save``. ``sign_in`` returns the payload fields."""
    secret = await run_library_call(sign_in, library_error=library_error, sign_in=True)
    return await save(factory, ring, ctx, provider, secret, public=public, expires_at=expires_at)


# ADR 0006 section 3: classbeat's local data about a MOWL account goes with its connection, so a
# reconnect as another MOWL account never reuses a link or an ``external_id`` of the old one. The
# operations go first: a worker recording an attach locks the operation row and then writes the
# link, so taking the same order here cannot deadlock with it.
_FORGET_MOWL = (
    text(
        "DELETE FROM external_operations WHERE owner_id = :owner_id "
        "AND kind = 'mowl_attach_spotify_playlist'"
    ),
    text("DELETE FROM mowl_playlist_links WHERE owner_id = :owner_id"),
)


async def _forget_mowl_account(db: AsyncSession, ctx: UserContext) -> None:
    for statement in _FORGET_MOWL:
        await db.execute(statement, {"owner_id": ctx.user_id})


async def disconnect(factory: Factory, ctx: UserContext, provider: str) -> bool:
    """Delete the row (local only: MOWL has no known revoke endpoint). For MOWL, the user's
    playlist links and attach operations go in the same transaction (ADR 0006 section 3)."""
    _check_provider(provider)
    async with user_session(factory, ctx) as db:
        # The row lock first: a job's external operation holds it (``hold``) while it writes
        # its ``pending`` row, so a disconnect either waits for that write (and then deletes
        # the row) or commits first (and the job sees the connection gone before any call).
        await db.execute(
            select(_C.id)
            .where(_C.owner_id == ctx.user_id, _C.provider == provider)
            .with_for_update()
        )
        if provider == "mowl":
            await _forget_mowl_account(db, ctx)
        result = await db.execute(
            delete(connections)
            .where(_C.owner_id == ctx.user_id, _C.provider == provider)
            .returning(_C.id)
        )
        return result.first() is not None


async def hold(db: AsyncSession, ctx: UserContext, opened: OpenConnection) -> bool:
    """In the caller's transaction (a job's fenced step 1 of an external operation, ADR 0005
    section 7): share-lock the connection row the job opened, if it is still that version and
    ``connected``. False when it was disconnected, reconnected (another ``wrapped_dek``, maybe
    another account) or flipped meanwhile, so no call goes out with credentials that are no
    longer the user's connection. The lock lasts until the caller commits; ``disconnect``
    takes the row lock first, so the two never deadlock."""
    row = (
        await db.execute(
            select(_C.id)
            .where(
                _C.id == opened.id,
                _C.owner_id == ctx.user_id,
                _C.wrapped_dek == opened.version,
                _C.status == "connected",
            )
            .with_for_update(read=True)
        )
    ).first()
    return row is not None


async def delete_all_in(db: AsyncSession, ctx: UserContext) -> list[str]:
    """For ``keys wipe``: delete every row of the user in the caller's transaction. Returns the
    affected providers (sorted). The MOWL links and attach operations go too, as on a
    disconnect (the connection rows locked first, as ``disconnect`` does)."""
    await db.execute(select(_C.id).where(_C.owner_id == ctx.user_id).with_for_update())
    await _forget_mowl_account(db, ctx)
    result = await db.execute(
        delete(connections).where(_C.owner_id == ctx.user_id).returning(_C.provider)
    )
    return sorted(result.scalars().all())


async def vacuum_tables(connection: AsyncConnection) -> None:
    """``keys wipe``: ``VACUUM (FULL)`` rewrites the table files without the deleted rows
    (an autocommit connection of the owner role)."""
    await connection.execute(text("VACUUM (FULL) public.connections"))

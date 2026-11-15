"""SwedeBeat connect through the owner's gateway (ADR 0035 sections 3 to 5; ADR 0015 for what
stays): email and password → one ``POST /auth/login`` → a bearer, stored encrypted like every
connection (ADR 0004). The password is discarded. classbeat never calls SwedeBeat itself.

- **Availability**: the gateway URL **and** the client key are set
  (``swedebeat_gateway.key_policy.gateway_available``). Without them:
  ``SwedebeatRefusal("app_key_unavailable")`` before anything is sent.
- **The sign-out consent** (section 4): the gateway always takes the session from the SwedeBeat
  phone app, so a connect is made only with ``force`` (the user ticked the box); without it
  ``SwedebeatRefusal("sign_out_required")`` and nothing is sent. ``force`` is sent in the body.
- **One device id per user**, derived and never random: HMAC-SHA256 of the user id under the app
  secret key, 16 hex characters. The same on every connect, reconnect and after a disconnect, and
  never stored.
- **Store**: ``{"bearer"}`` with ``expires_at = now + expires_in`` and ``public = {"email"}``.
  The bearer is never library credentials (``build_credentials`` refuses ``swedebeat``): only
  ``open_bearer`` opens it, through ``UserContext``, for the gateway client alone.
- **Status from the answer only** (``swedebeat_gateway.status_policy``): ``unknown client`` and a
  ``403`` are this server's configuration (``app_key_unavailable``, an operator log line, nothing
  stored), never the user's password.

**Opening** (``open_bearer``): ``not_connected`` without a ``connected`` row or when the row is
past ``expires_at`` (or has none: every gateway-era row has one), without a request and without a
write; a legacy ``{token, device_id}`` row of the direct-SwedeBeat era flips to
``needs_reconnect``; ``key_unavailable`` and ``decrypt_failed`` as ``open_connection``. Nothing
here logs a value: only categories and the row id.
"""

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.connections.credentials import Credentials, OpenConnection
from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.repository import save
from classbeat.connections.row_crypto import open_row
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.db import system_session, user_session
from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import LOGIN_SECONDS
from classbeat.flags import effective
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_gateway.key_policy import gateway_available
from classbeat.swedebeat_gateway.login_policy import BEARER_MAX, BEARER_MIN, LoginAnswer

logger = logging.getLogger(__name__)

PROVIDER = "swedebeat"
DEVICE_ID_CHARS = 16
_DEVICE_LABEL = b"classbeat/swedebeat-device/v1\x00"
_LEGACY_FIELDS = frozenset({"token", "device_id"})  # ADR 0015's payload, before the gateway
_C = connections.c


class SwedebeatRefusal(Exception):
    """A connect failure that is no ``ConnectionFailure`` category (those are shared with the
    database's checks): ``app_key_unavailable`` or ``sign_out_required``. ``args == (reason,)``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SwedebeatAccess:
    """What the account page needs to know before it shows the form."""

    enabled: bool  # the effective flag (environment switch and admin flag)
    available: bool  # the server has the gateway's URL and its client key


@dataclass(frozen=True, slots=True, eq=False)
class SwedebeatBearer:
    """An opened SwedeBeat connection for one job: the row (``connection``: id, generation and
    version, for ``record_result`` and ``mark_needs_reconnect``; its credentials are empty), the
    bearer and its ``expires_at``. Never logged, stored or serialized."""

    connection: OpenConnection
    bearer: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"SwedebeatBearer(id={self.connection.id}, bearer=...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a bearer is never serialized")


def device_id_for(secret: bytes, user_id: UUID) -> str:
    """The user's one stable device id (see the module docstring)."""
    mac = hmac.new(secret, _DEVICE_LABEL + user_id.bytes, hashlib.sha256)
    return mac.hexdigest()[:DEVICE_ID_CHARS]


def live(expires_at: datetime | None, now: datetime) -> bool:
    """A stored bearer may still be sent: it has an ``expires_at`` and it is in the future."""
    return expires_at is not None and expires_at > now


async def _login(
    key: SecretStr, email: str, password: str, device_id: str, *, force: bool
) -> LoginAnswer:
    """One gateway login. ``ConnectionFailure`` or ``SwedebeatRefusal``, chained to nothing."""
    failure: gateway.GatewayFailure | None = None
    unreachable = False
    try:
        async with opener.open_client(LOGIN_SECONDS) as client:
            return await gateway.login(client, key, email, password, device_id, force=force)
    except gateway.GatewayFailure as refused:
        failure = gateway.GatewayFailure(refused.category, refused.retry_after)
    except OutboundBlocked:
        unreachable = True  # no origin installed: this server cannot reach the gateway
    except ValueError:
        # Outside the gateway's bounds (the route bounds the form first); the text names a field.
        failure = gateway.GatewayFailure("invalid_credentials")
    # Outside the except blocks: nothing is chained onto what is raised.
    if unreachable or (failure is not None and failure.category == "app_key_unavailable"):
        logger.error(
            "swedebeat gateway refused the client key",
            extra={"category": "app_key_unavailable", "action": "sign_in"},
        )
        raise SwedebeatRefusal("app_key_unavailable")
    category = "unknown" if failure is None else failure.category
    retry_after = None if failure is None else failure.retry_after
    logger.warning(
        "swedebeat gateway login failed", extra={"category": category, "action": "sign_in"}
    )
    raise ConnectionFailure(category, retry_after)


async def connect_swedebeat(
    factory: async_sessionmaker[AsyncSession],
    ring: KeyRing,
    ctx: UserContext,
    email: str,
    password: str,
    *,
    force: bool,
    device_secret: bytes,
    key: SecretStr | None,
) -> UUID:
    """Sign in through the gateway and store the bearer for ``ctx``'s user (connect or reconnect).
    Returns the row id, kept across reconnects.

    ``ConnectionFailure``: ``invalid_credentials``, ``rate_limited``, ``upstream_unavailable`` or
    ``unknown``. ``SwedebeatRefusal``: ``app_key_unavailable`` (nothing stored) or
    ``sign_out_required`` (nothing sent)."""
    if key is None:
        raise SwedebeatRefusal("app_key_unavailable")
    if force is not True:
        raise SwedebeatRefusal("sign_out_required")
    device_id = device_id_for(device_secret, ctx.user_id)
    answer = await _login(key, email, password, device_id, force=True)
    expires_at = datetime.now(UTC) + timedelta(seconds=answer.expires_in)
    return await save(
        factory,
        ring,
        ctx,
        PROVIDER,
        {"bearer": answer.bearer},
        public={"email": email},
        expires_at=expires_at,
    )


def _bearer_of(plaintext: bytes) -> str | bool | None:
    """The stored bearer; ``False`` for a legacy ``{token, device_id}`` row; ``None`` otherwise."""
    try:
        payload: Any = json.loads(plaintext)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    fields = set(payload)  # pyright: ignore[reportUnknownArgumentType]
    if fields == _LEGACY_FIELDS:
        return False
    bearer: object = payload.get("bearer") if fields == {"bearer"} else None  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(bearer, str) or not BEARER_MIN <= len(bearer) <= BEARER_MAX:
        return None
    return bearer if all("!" <= c <= "~" for c in bearer) else None


async def _flip(db: AsyncSession, row: Any, category: str) -> None:
    """``needs_reconnect`` for the row read (same generation and data key): status, error, time."""
    await db.execute(
        update(connections)
        .where(
            _C.id == row.id,
            _C.owner_id == row.owner_id,
            _C.generation == row.generation,
            _C.wrapped_dek == row.wrapped_dek,
        )
        .values(status="needs_reconnect", last_error=category, updated_at=func.now())
    )


async def open_bearer(
    factory: async_sessionmaker[AsyncSession],
    ring: KeyRing,
    ctx: UserContext,
    *,
    now: datetime | None = None,
) -> SwedebeatBearer:
    """The user's stored bearer (see the module docstring); ``ConnectionFailure`` otherwise."""
    at = now or datetime.now(UTC)
    failure: str | None = None
    opened: SwedebeatBearer | None = None
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                select(
                    _C.id, _C.owner_id, _C.status, _C.secret, _C.secret_nonce, _C.wrapped_dek,
                    _C.dek_nonce, _C.key_id, _C.generation, _C.expires_at,
                ).where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER)
            )
        ).first()  # fmt: skip
        if row is None or row.status != "connected":
            failure = "not_connected"
        else:
            failure, plaintext = await open_row(db, ring, ctx.user_id, PROVIDER, row)
            bearer = None if plaintext is None else _bearer_of(plaintext)
            if failure == "decrypt_failed":
                await _flip(db, row, "decrypt_failed")
            elif failure is not None:
                pass  # key_unavailable: an operator error, the row is not changed
            elif bearer is False:
                failure = "needs_reconnect"  # ADR 0035 section 3, item 6: no request is made
                await _flip(db, row, "needs_reconnect")
            elif not isinstance(bearer, str):
                failure = "unknown"
            elif not live(row.expires_at, at):
                failure = "not_connected"  # past its lifetime: a reconnect, never a request
            else:
                connection = OpenConnection(
                    row.id, PROVIDER, Credentials(), bytes(row.wrapped_dek), int(row.generation)
                )
                opened = SwedebeatBearer(connection, bearer, row.expires_at)
    if opened is None:
        category = failure or "unknown"
        if row is not None and category != "not_connected":
            logger.warning(
                "connection unusable", extra={"category": category, "connection_id": str(row.id)}
            )
        raise ConnectionFailure(category)
    return opened


async def swedebeat_email(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext
) -> str | None:
    """The Swedebeat email of the user's connection (shown on the account page), if any."""
    async with user_session(factory, ctx) as db:
        value = (
            await db.execute(
                select(_C.public["email"].astext).where(
                    _C.owner_id == ctx.user_id, _C.provider == PROVIDER
                )
            )
        ).scalar_one_or_none()
    return value if isinstance(value, str) and value else None


async def swedebeat_enabled(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> bool:
    """Only the effective flag: no read of the gateway settings (for routes that never need it)."""
    async with system_session(factory) as db:
        return await effective.swedebeat_enabled(db, settings.swedebeat_enabled)


async def swedebeat_access(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> SwedebeatAccess:
    """The effective flag (environment switch, then the admin flag) and whether this server can
    reach the gateway (its URL and its client key, ADR 0035 section 2)."""
    async with system_session(factory) as db:
        enabled = await effective.swedebeat_enabled(db, settings.swedebeat_enabled)
    return SwedebeatAccess(enabled=enabled, available=gateway_available(settings))

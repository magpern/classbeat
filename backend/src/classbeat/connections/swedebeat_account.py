"""Swedebeat connect (ADR 0015): email and password → the library's sign-in → token and device
id, stored encrypted like every connection (ADR 0004). The password is discarded.

Same shape as ``mowl_account``: the sign-in runs first, outside any transaction, inside
``run_library_call`` (the library's exception never leaves, ``ConnectionFailure`` has no chain);
``save`` then takes the row lock and updates or inserts. Differences:

- **The app key** is Swedebeat's, not classbeat's, and is never in the image: it arrives from the
  optional ``swedebeat_app`` secret file (``Settings.swedebeat_keys``) and is handed to the library
  in ``Credentials.swedebeat_app`` for this one call. Without it: ``SwedebeatRefusal(
  "app_key_unavailable")`` before anything is sent.
- **One device id per user**, derived and never random: HMAC-SHA256 of the user id under the app
  secret key, 16 hex characters (the library's own length). It is the same on every connect,
  reconnect and after a disconnect, so a reconnect is the same "device" to Swedebeat, and two
  users never share one.
- **``force`` is False unless the user confirms**: the library's ``force=True`` takes the session
  from another device (it can sign the user's phone out). When Swedebeat says another device holds
  the session, the answer is ``SwedebeatRefusal("device_in_use")`` and nothing is stored; the user
  may repeat the connect with ``force`` after a plain-words confirmation.
- The stored ``public`` holds the Swedebeat email only, so the account page can show whose
  account it is. Never the token, the device id or the password.

The library flattens a refusal that arrives as HTTP 200 into a message. The only decision taken
from a message is "another device holds the session", by a fixed list of phrases and only when
there is no HTTP cause; the message itself is never stored, logged or returned. Any other refusal
is ``invalid_credentials`` (the safe reading: no force). The real wording is an owner check at
live activation (ADR 0015 section 9).
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError
from uuid import UUID

from mowl import swedebeat
from mowl.errors import MowlError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.config import Settings
from classbeat.connections.credentials import Credentials
from classbeat.connections.errors import run_library_call
from classbeat.connections.repository import save
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.db import system_session, user_session
from classbeat.flags import effective

PROVIDER = "swedebeat"
DEVICE_ID_CHARS = 16
_DEVICE_LABEL = b"classbeat/swedebeat-device/v1\x00"
# Lower-case phrases of a "another device holds the session" refusal (see the module docstring).
_DEVICE_PHRASES = ("another device", "other device", "already logged", "already signed", "in use")
_CONFLICT = 409


class SwedebeatRefusal(Exception):
    """A connect failure that is no ``ConnectionFailure`` category (those are shared with the
    database's checks): ``device_in_use`` or ``app_key_unavailable``. ``args == (reason,)``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SwedebeatAccess:
    """What the account page needs to know before it shows the form."""

    enabled: bool  # the effective flag (environment switch and admin flag)
    available: bool  # the server holds Swedebeat's app key


def device_id_for(secret: bytes, user_id: UUID) -> str:
    """The user's one stable device id (see the module docstring)."""
    mac = hmac.new(secret, _DEVICE_LABEL + user_id.bytes, hashlib.sha256)
    return mac.hexdigest()[:DEVICE_ID_CHARS]


def _device_taken(exc: MowlError) -> bool:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return cause.code == _CONFLICT
    if cause is not None:
        return False
    text = str(exc).lower()
    return any(phrase in text for phrase in _DEVICE_PHRASES)


def _sign_in(
    email: str, password: str, force: bool, device_id: str, app: Mapping[str, str]
) -> dict[str, str] | None:
    """The library's sign-in (synchronous; runs in a worker thread). The payload, or ``None``
    when another device holds the session and ``force`` is off."""
    credentials = Credentials(swedebeat_device_id=device_id, swedebeat_app=dict(app))
    taken = False
    try:
        swedebeat.login(credentials, email, password, force=force)
    except MowlError as exc:
        taken = _device_taken(exc)
        if not taken:
            raise
    if taken:
        return None  # outside the except block: nothing chained onto the answer
    return {
        "token": str(credentials.swedebeat_token),
        "device_id": str(credentials.swedebeat_device_id),
    }


async def connect_swedebeat(
    factory: async_sessionmaker[AsyncSession],
    ring: KeyRing,
    ctx: UserContext,
    email: str,
    password: str,
    *,
    force: bool,
    device_secret: bytes,
    app: Mapping[str, str] | None,
) -> UUID:
    """Sign in to Swedebeat and store the token and device id for ``ctx``'s user (connect or
    reconnect). Returns the row id, kept across reconnects.

    ``ConnectionFailure``: ``invalid_credentials``, ``rate_limited``, ``upstream_unavailable`` or
    ``unknown``. ``SwedebeatRefusal``: ``app_key_unavailable`` (nothing sent) or
    ``device_in_use`` (nothing stored)."""
    if app is None:
        raise SwedebeatRefusal("app_key_unavailable")
    device_id = device_id_for(device_secret, ctx.user_id)
    secret = await run_library_call(
        lambda: _sign_in(email, password, force, device_id, app), sign_in=True
    )
    if secret is None:
        raise SwedebeatRefusal("device_in_use")
    return await save(factory, ring, ctx, PROVIDER, secret, public={"email": email})


async def swedebeat_email(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext
) -> str | None:
    """The Swedebeat email of the user's connection (shown on the account page), if any."""
    async with user_session(factory, ctx) as db:
        value = (
            await db.execute(
                select(connections.c.public["email"].astext).where(
                    connections.c.owner_id == ctx.user_id, connections.c.provider == PROVIDER
                )
            )
        ).scalar_one_or_none()
    return value if isinstance(value, str) and value else None


async def swedebeat_access(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> SwedebeatAccess:
    """The effective flag (environment switch, then the admin flag) and the key's presence."""
    async with system_session(factory) as db:
        enabled = await effective.swedebeat_enabled(db, settings.swedebeat_enabled)
    return SwedebeatAccess(enabled=enabled, available=settings.swedebeat_keys() is not None)

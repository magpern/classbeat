"""MOWL connect (ADR 0004 §4): email and password → the library's sign-in → the token, stored
encrypted. The password is discarded.

The password lives only in the request body model, the closure handed to the worker thread and
the library's own frames for the length of one sign-in. It is never stored, logged, put in a URL,
kept in an exception or sent to a model: the sign-in runs inside ``run_library_call``, whose
``ConnectionFailure`` has neither ``__cause__`` nor ``__context__`` and only the category in its
``args`` (§5). The sign-in runs first, outside any transaction, and ``save`` then takes the row
lock (``SELECT ... FOR UPDATE``) and updates or inserts (§4).

The MOWL token is a 36-character GUID with no expiry claim, so its lifetime is unknown (§6):
``expires_at`` stays ``NULL`` and a 401 or 403 on use flips the connection to ``needs_reconnect``
(``repository.use``).
"""

from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.repository import connect
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing

PROVIDER = "mowl"


def _sign_in(email: str, password: str) -> dict[str, str]:
    """The library's sign-in (synchronous; runs in a worker thread). Returns the payload."""
    return {"token": mowl_api.login(email, password)}


async def connect_mowl(
    factory: async_sessionmaker[AsyncSession],
    ring: KeyRing,
    ctx: UserContext,
    email: str,
    password: str,
) -> UUID:
    """Sign in to MOWL and store the token for ``ctx``'s user (connect or reconnect).

    ``ConnectionFailure``: ``invalid_credentials``, ``rate_limited``, ``upstream_unavailable`` or
    ``unknown`` (ADR 0004 §5). Returns the row id, kept across reconnects.
    """
    return await connect(factory, ring, ctx, PROVIDER, lambda: _sign_in(email, password))

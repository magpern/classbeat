"""The Spotify connection (ADR 0009): bring your own Client ID, OAuth with PKCE, token refresh,
error categories and the pool consent.

- ``spotify_call`` (``access``): the only way to call the Spotify API with a user's token.
- ``SpotifyHttp`` (``http``): classbeat's own token endpoint and ``GET /me`` client.
- ``classify_use``, ``sentence`` (``errors``): section 8's categories and plain sentences.
- ``forget_account``, ``forget_pool``: section 7's deletions in the caller's transaction.
- The routes (``classbeat.spotify.routes``) are imported by the web app only, never here, so the
  worker never loads FastAPI's request machinery for this package.

Everything that reads or writes owned rows or the encrypted tokens lives in
``classbeat.connections`` (the only package that may, ``tests/test_crypto_guard.py`` and
``tests/test_session_guard.py``); this package holds the HTTP, the flow logic and the routes.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import spotify_data
from classbeat.context import UserContext
from classbeat.spotify.access import refresh_if_needed, spotify_call
from classbeat.spotify.errors import CATEGORIES, SENTENCES, classify_use, sentence
from classbeat.spotify.http import SpotifyHttp


async def forget_account(db: AsyncSession, ctx: UserContext) -> None:
    """Disconnect and an account change (ADR 0009 section 7), in the caller's transaction after
    it took the Spotify connection row lock: every Spotify operation row (pool and final, any
    state), the consent row, and every live class playlist detached. The caller deletes or
    rewrites the connection row (its tokens) itself."""
    await spotify_data.forget_spotify_account(db, ctx.user_id)


async def forget_pool(db: AsyncSession, ctx: UserContext) -> None:
    """The narrower part: the consent row and the pool operation rows (a consent revoke, a
    Client ID change). ADR 0008 section 2 calls this ``forget_pool``; in
    ``classbeat.connections.spotify_data`` it is ``revoke_pool`` (``forget_pool`` there is the
    operation rows alone, which ``pool_missing`` uses)."""
    await spotify_data.revoke_pool(db, ctx.user_id)


__all__ = [
    "CATEGORIES",
    "SENTENCES",
    "SpotifyHttp",
    "classify_use",
    "forget_account",
    "forget_pool",
    "refresh_if_needed",
    "sentence",
    "spotify_call",
]

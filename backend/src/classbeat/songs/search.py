"""The ``Searcher`` a pick lends ``pick_pool`` (ADR 0008 section 3): one Spotify search per call.

The query is the one ``pick_pool.build_query`` built (the model's words and the backend's
``year:``); the request is ``mowl.spotify_web.search_tracks`` with the library's own fixed path
and limit (at most 10) and no market (Spotify then uses the account's). It runs through
``classbeat.playlists.common.call`` (``spotify_call``) for the connection the job opened and only
that one: a reconnect, a Client ID change or another account in between sends nothing
(``not_connected``). The library call is blocking: ``spotify_call`` runs it in a worker thread
(``connections.run_library_call``, ``asyncio.to_thread``), never on the worker's event loop, so the
heartbeat and the other runs go on while Spotify answers. The token never leaves
``classbeat.connections``; the stage only ever sees the library's track dicts, which the search
registry reduces to refs and cleaned text (and the backend alone keeps Spotify's ``explicit`` flag
of each, ``songs.search_hits_calc``).

A refusal that is worth another try (``rate_limited``, ``upstream_unavailable``) is
``SearchUnavailable``: the model is told search is unavailable right now (a fixed sentence) and may
go on with what it has. Every other failure (the connection is gone, ``invalid_grant``, a quota)
ends the stage and the job with its category.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from mowl import spotify_web

from classbeat.agent.pick_pool import Searcher, SearchUnavailable
from classbeat.connections import ConnectionFailure, Credentials, OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobRun
from classbeat.playlists import common
from classbeat.songs.search_hits_calc import hits as search_hits

TRANSIENT: Final = frozenset({"rate_limited", "upstream_unavailable"})


def job_searcher(run: JobRun, keyring: KeyRing, opened: OpenConnection) -> Searcher:
    async def search(query: str) -> Sequence[Mapping[str, Any]]:
        def read(creds: Credentials) -> list[Any]:
            # The library's ``search_tracks`` drops Spotify's ``explicit`` flag, so this is its
            # request (same path, ``type=track``, its limit, no market) read through its own
            # ``_track`` with the flag kept (#1340; ``tests/test_mowl_library.py`` pins both).
            params = {"q": query, "type": "track", "limit": spotify_web.SEARCH_LIMIT}
            answer = spotify_web.call(creds, "GET", "search", params)
            return search_hits(answer, spotify_web._track)  # pyright: ignore[reportPrivateUsage]

        try:
            hits = await common.call(run.factory, keyring, run.ctx, opened, read)
        except ConnectionFailure as failure:
            if failure.category in TRANSIENT:
                raise SearchUnavailable from None
            raise
        return [h for h in hits if isinstance(h, Mapping)]  # pyright: ignore[reportUnknownVariableType]

    return search

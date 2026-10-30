"""The Spotify playlist writes (ADR 0008 section 2, issue OP1): the external-operation kinds
``spotify_create_playlist`` and ``spotify_replace_items``, and the pool and final-playlist steps
built on them.

- ``classbeat.playlists.common``: the runtime (token client, refresh margin, listing lag), the
  track-list hash, the owner check, the read-back and the one way these writes call Spotify;
- ``classbeat.playlists.create``: ``spotify_create_playlist`` (one request), its reconciler (the
  user's listing, by ``ref``) and ``on_succeeded`` (the pool's id, the class playlist row);
- ``classbeat.playlists.replace``: ``spotify_replace_items`` (owner check, write, read-back), its
  reconciler (by comparison) and ``on_succeeded`` (the pool's hash, a verified class playlist);
- ``classbeat.playlists.pool``: ``write_pool`` with the decide-step guard (connection, consent,
  pool), the supersede rule and the ``pool_missing`` forget;
- ``classbeat.playlists.final``: ``create_final_playlist`` and ``fill_final_playlist``.

Importing this package registers both kinds (the worker and the web app import it, so the
reconciler of the user's Check again on a final-playlist write is there). Every write goes through
``classbeat.operations.perform`` and the job's fence; nothing here opens a ``user_session``.
"""

from classbeat.playlists.common import POOL_NAME, Runtime, configure
from classbeat.playlists.create import CREATE
from classbeat.playlists.final import ClassPlaylist, create_final_playlist, fill_final_playlist
from classbeat.playlists.pool import Pool, PoolWrite, open_spotify, write_pool
from classbeat.playlists.replace import REPLACE

__all__ = [
    "CREATE",
    "POOL_NAME",
    "REPLACE",
    "ClassPlaylist",
    "Pool",
    "PoolWrite",
    "Runtime",
    "configure",
    "create_final_playlist",
    "fill_final_playlist",
    "open_spotify",
    "write_pool",
]

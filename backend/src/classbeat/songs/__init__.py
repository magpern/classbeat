"""Song picking's job kinds (ADR 0008 sections 4 to 7, M4): a class designed on songs Claude picks.

- ``classbeat.songs.kind``: ``design_with_songs`` (registered in ``classbeat.jobs.KINDS`` on
  import, for both the worker and the web app), with the ``phase_two`` seam of issue SJ2;
- ``classbeat.songs.inputs``: its input model (``seed`` for "Ask for changes", ``resume``);
- ``classbeat.songs.admit``: ``admit`` (``song_picking_off``, ``receipt_invalid``,
  ``consent_missing``, ``pool_busy``, the seed rule) and ``approve_check`` (the list re-hashed,
  ``account_changed``), both in the jobs API's transaction;
- ``classbeat.songs.pick``: phase 1 (pool search, pool write and MOWL attach, the analysis with the
  pool's sync wait, the choice) up to ``AwaitApproval``;
- ``classbeat.songs.song_list``: the ``song_list`` revision, its canonical hash, ``build``,
  ``store`` and the re-checking ``load`` phase 2 uses;
- ``classbeat.songs.search``: the Spotify search lent to ``pick_pool``;
- ``classbeat.songs.config``: the settings both processes ``configure`` at start.

A job kind's own package (job kinds live outside ``classbeat.jobs``): it writes only through the
job's fence, reads the user's rows in the jobs API only through the session it is handed, and
calls Spotify and MOWL only through ``classbeat.connections``, ``classbeat.playlists`` and
``classbeat.operations``.
"""

from classbeat.songs import song_list
from classbeat.songs.config import SongConfig, configure
from classbeat.songs.inputs import DesignWithSongsInput, Resume, Seed
from classbeat.songs.kind import DESIGN_WITH_SONGS, KIND

__all__ = [
    "DESIGN_WITH_SONGS",
    "KIND",
    "DesignWithSongsInput",
    "Resume",
    "Seed",
    "SongConfig",
    "configure",
    "song_list",
]

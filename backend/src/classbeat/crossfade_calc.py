"""The crossfade classbeat models for a Spotify class (#1427, ADR 0006 amendment).

MOWL plays a Spotify class as a playlist whose tracks each end ``CROSSFADE_SEC`` early: the owner's
read-only capture of the native MOWL app (2026-11-20, issue #580) saw ``SeekToPosition`` carry
``CrossFadeInMilliSeconds: 9540``. One capture (one account, one playlist) exists, so this is one
constant, not a per-user setting; the stored ``crossfade_sec`` of a list and the ``crossfade`` key
of a map keep a per-user value possible later without a schema change.

It replaces the wheel's 9.0 (``mowl.spotify.CROSSFADE_SEC``), an empirical fit that mowl-workouts
0.5.0 made from four official rides and that sits inside the noise of 9.54. The vendored wheel is
not rebuilt, so every map the library builds or converts at 9.0 goes through ``with_classbeat``
before classbeat uses it. SwedeBeat studio classes are unaffected: their crossfades are per
transition and their list has ``crossfade_sec`` 0.

A pure module: no I/O.
"""

from collections.abc import Mapping
from typing import Any, Final

from mowl import spotify

CROSSFADE_SEC: Final = 9.54


def with_classbeat(m: dict[str, Any]) -> dict[str, Any]:
    """The Spotify map as MOWL plays it: ``m`` itself when it already has ``CROSSFADE_SEC``,
    otherwise a copy shifted by ``spotify.with_crossfade`` (a map without the key has its tracks
    back to back). A stored map is never changed."""
    return spotify.with_crossfade(m, CROSSFADE_SEC)


def track_shift_sec(m: Mapping[str, Any], track: int) -> float:
    """How far track ``track`` (1-based) of the stored map ``m`` moves in ``with_classbeat``:
    ``spotify.with_crossfade``'s ``(map crossfade - CROSSFADE_SEC) * (track - 1)``, for a caller
    that reads only a track's times. A map without a ``crossfade`` key is back to back (0)."""
    return (float(m.get("crossfade", 0.0)) - CROSSFADE_SEC) * (track - 1)

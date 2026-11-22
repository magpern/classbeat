"""The list spotdown times for a ``design_draft`` on the user's MOWL playlist (ADR 0040 amendment
"spotdown is the only analysis path", items 3 and 5; #1407). Pure: no database, no network.

The class plays the user's playlist as MOWL analysed it, so **every track** of the map is in the
list, in the map's order (a track listed twice stays twice): nothing is left out, reordered or
chosen, and each position is the map's track index, which stage D (``phase2_spotdown``) reads by.

- A track spotdown cannot align (no Spotify track id: a local file, an episode) or with no length
  is **missing**, and a song of the user's playlist has no spare: ``analysis_failed`` (item 5).
- More tracks than a revision records (``revision_spotdown_model.MAX_TRACKS``) is refused
  ``invalid_input``; so is a map with no track.
- A track MOWL has no BPM for yet keeps the library's own guess (``GUESS_BPM``, as
  ``mowl.spotify.build_map`` uses it; the revision still lists it in ``unknown_bpm``). The BPM is
  used only for the slot fit, and a fixed song that does not fit is accepted with a warning.

Title, artist and length are MOWL's, never a model's; the roles are positional
(``playlist_list_policy.role_at``) and ``new`` is False (the user's own songs).
"""

from collections.abc import Mapping
from typing import Any, Final, Literal

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.songs import CROSSFADE_SEC, total_sec
from classbeat.design.revision_spotdown_model import MAX_TRACKS
from classbeat.songs.playlist_list_policy import SPOTIFY_TRACK_ID, role_at

GUESS_BPM: Final = 120.0  # ``mowl.spotify.build_map``'s guess for a track with no BPM yet
MODE: Final = "pick"  # the list is never stored: the mode only satisfies ``ChosenList``

Refused = Literal["analysis_failed", "invalid_input"]


def _bpm(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return GUESS_BPM
    return float(value)


def whole_list(analysis_map: Mapping[str, Any]) -> ChosenList | Refused:
    """Every track of ``analysis_map`` as a ``ChosenList`` (module docstring), or the category
    the job ends with."""
    tracks = analysis_map.get("tracks")
    listed = list(tracks) if isinstance(tracks, list) else []  # pyright: ignore[reportUnknownArgumentType]
    if not listed or len(listed) > MAX_TRACKS:
        return "invalid_input"
    songs: list[ChosenSong] = []
    for n, t in enumerate(listed):  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(t, Mapping):
            return "invalid_input"
        spotify_id = str(t.get("spotify_id"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        sec = t.get("sec")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not SPOTIFY_TRACK_ID.fullmatch(spotify_id):
            return "analysis_failed"  # spotdown cannot align it to a Spotify track
        if isinstance(sec, bool) or not isinstance(sec, int | float) or sec < 1:
            return "analysis_failed"
        role = role_at(n, len(listed))
        title, artist = str(t.get("title") or ""), str(t.get("artist") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        bpm = _bpm(t.get("bpm"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        songs.append(ChosenSong(spotify_id, title, artist, role, bpm, round(sec), False, None))
    total = round(total_sec([s.length_sec for s in songs]))
    return ChosenList(tuple(songs), total, total, CROSSFADE_SEC, MODE, 0, "")

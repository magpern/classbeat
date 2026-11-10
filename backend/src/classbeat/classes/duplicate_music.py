"""What a duplicate copies of the source class's music (ADR 0007 amendment of 2026-11-03, part D;
issue #547; split out of ``classes.duplicate`` by #907). No database or network access.

Only the user's own playlist or mix choice is copied (a class on picked songs names the source
class's playlists and is not), the revision's music loses the source class's song list, and the
beat-one corrections follow the chosen revision's fingerprint (DU3, #585).
"""

import re
from typing import Any, Final

from classbeat.design import music_kind

HASH: Final = re.compile(r"^[0-9a-f]{64}$")
SPOTIFY_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")
MIX_ID: Final = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
MIX_SOURCE: Final = "mowl_swedebeat_mix"
PLAYLIST_SOURCE: Final = "mowl_spotify_playlist"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


def music_source_copy(source: Any) -> dict[str, str] | None:
    """The class's music source as the user's own choice, or ``None``: a class on picked songs
    (``origin: "song_pick"``) names the source class's playlists and is not copied."""
    found = _dict(source)
    if (
        found.get("source") == PLAYLIST_SOURCE
        and set(found) == {"source", "spotify_playlist_id"}
        and isinstance(pid := found.get("spotify_playlist_id"), str)
        and SPOTIFY_ID.fullmatch(pid)
    ):
        return {"source": PLAYLIST_SOURCE, "spotify_playlist_id": pid}
    if (
        found.get("source") == MIX_SOURCE
        and set(found) == {"source", "mix_id"}
        and isinstance(mix := found.get("mix_id"), str)
        and MIX_ID.fullmatch(mix)
    ):
        return {"source": MIX_SOURCE, "mix_id": mix}
    if music_kind.is_none(found) and set(found) == {"source"}:
        return dict(
            music_kind.NO_MUSIC
        )  # a soundless class stays soundless (its kind never changes)
    return None


def music_copy(music: Any) -> dict[str, Any]:
    """The revision's music reference without the source class's song list."""
    copied = _dict(music)
    copied.pop("song_list_id", None)
    return copied


def corrections_fingerprint(source: dict[str, str] | None, music: Any) -> str | None:
    """The fingerprint whose corrections the copy gets, or ``None`` for none: the copy has a
    music source that is not ``none`` (``source`` is ``music_source_copy``'s answer) and the
    chosen revision's music is not ``none`` and has a well-formed fingerprint."""
    if source is None or music_kind.is_none(source) or music_kind.is_none(music):
        return None
    found = _dict(music).get("fingerprint")
    return found if isinstance(found, str) and HASH.fullmatch(found) else None

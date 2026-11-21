"""A kept song's spotdown timing carried into a "Change songs" list (ADR 0040, its SD-P6 stage E
amendment; plan section 6, "partial changes"; #1303). Pure: no I/O.

``revise_songs`` replaces only the slots asked; its phase 1 matches and analyses **only the new
songs** (stages A and B: a kept song is never in the pool). Without this, a kept song would lose
the spotdown timing its list recorded, so a change of one song would change the others. Here the
new list's ``spotdown_analysis`` record gets, for each kept song in its own slot, the parent
list's entry for that slot, as it was (``kept: true`` added): phase 2 then reads that analysis
again and verifies it against the track (stage D), exactly as for a new song.

Carried only when the new list has a record (the stage ran for this class and did not fall
back; else today's path for the whole class) and only for a slot the parent marked
(``timing_marks``: a ``spotdown`` use, ``approximate``) with exactly one entry
(``entries_by_position``), whose kept song is still in that slot and not new. A slot the new
record already names is never overwritten.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_sections_calc import entries_by_position

KEPT: Final = "kept"


def with_kept(
    record: Mapping[str, Any],
    parent: Mapping[str, Any],
    kept: Sequence[tuple[int, str]],
    listed: Sequence[tuple[str, bool]],
) -> dict[str, Any]:
    """``record`` (``{KEY: ...}`` or empty) with the kept songs' parent entries. ``kept``: each
    kept song's slot and Spotify id; ``listed``: the new list's ``(spotify_id, new)`` in order."""
    if KEY not in record:
        return dict(record)
    marked = {p for p, _, _ in timing_marks(parent)}
    entries = entries_by_position(parent)
    new = dict(record[KEY])
    songs = list(new["songs"])
    taken = {e.get("position") for e in songs}
    for slot, spotify_id in kept:
        if slot not in marked or slot not in entries or slot in taken:
            continue
        if not 0 <= slot < len(listed) or listed[slot] != (spotify_id, False):
            continue
        songs.append({**entries[slot], "position": slot, KEPT: True})
    new["songs"] = sorted(songs, key=lambda e: e["position"])
    return {**record, KEY: new}

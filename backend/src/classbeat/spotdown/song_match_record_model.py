"""The record of a song pick's match stage, kept in the ``song_list`` revision's provenance under
``spotdown_match`` (ADR 0040 section 6, plan section 4a; SD-P6 stage A, #1303). Pure: no I/O.

Written only when the match ran and the class did not fall back to today's path; a list made
without it is stored exactly as before. Per song of the list (by its position): the final match
status, its ``use`` (``song_match_policy``), band and confidence; a song kept from the seed list
has no entry. Per swap: the position of the replacement, the original as it was wanted (its
Spotify id, title and artist: what an undo needs, as ADR 0036 section 3.3 stores it), the reason
and the round. No source reference, client ref, URL, cached analysis id or scope: the sources stay
in memory for the run (``TrackMatch.sources``).
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.spotdown.song_match_policy import POLICY_VERSION, TrackMatch
from classbeat.spotdown.song_swap_calc import Swap

KEY: Final = "spotdown_match"
RECORD_VERSION: Final = 1


def _song(position: int, match: TrackMatch) -> dict[str, Any]:
    entry: dict[str, Any] = {"position": position, "status": match.status, "use": match.use}
    if match.band is not None:
        entry["band"] = match.band
    if match.confidence is not None:
        entry["confidence"] = round(match.confidence, 3)
    return entry


def _swap(position: int, swap: Swap) -> dict[str, Any]:
    original = {"spotify_id": swap.original_id, "title": swap.title, "artist": swap.artist}
    return {"position": position, "original": original, "reason": swap.reason, "round": swap.round}


def match_record(
    listed: Sequence[str],
    matches: Mapping[str, TrackMatch],
    swaps: Mapping[str, Swap],
    *,
    rounds: int,
    candidates: int,
    missing: int,
) -> dict[str, Any]:
    """``{KEY: record}`` for the list's songs (``listed``: their Spotify ids, in order)."""
    versions = sorted({m.matcher_version for m in matches.values() if m.matcher_version})
    record = {
        "v": RECORD_VERSION,
        "policy_version": POLICY_VERSION,
        "matcher_versions": versions,
        "rounds": rounds,
        "candidates": candidates,
        "missing": missing,
        "songs": [_song(n, matches[s]) for n, s in enumerate(listed) if s in matches],
        "swaps": [_swap(n, swaps[s]) for n, s in enumerate(listed) if s in swaps],
    }
    return {KEY: record}

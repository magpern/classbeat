"""A Spotify draft as the edit's state (ADR 0042 sections 3 to 5, #1424). Pure: no I/O.

``studio_edit_calc.EditState`` is what ``studio_edit_ops.edited`` computes an edit from. A Spotify
draft has no mix spec, no list of studio tracks, no proposal and no pins: it plays whole songs in
the list's order (ADR 0021), so those are empty here, and what is left is the workout, its overlay
and the one map the class was fitted to (``fixed_map``, ``design.revision_edit_map``). So:

- **a workout or overlay edit** (drag, move, split, merge, set, duration, lock, unlock, use the
  AI's value, clear) runs the same operations, locks and checks as a studio draft's, with
  ``agent.design.check`` on that map and the plan gates;
- **an audio edit** (a cut pin) and **an anchor** name a SwedeBeat piece and a catalogue event: a
  Spotify draft has neither, so they are refused ``audio_edit_unavailable`` before anything is
  computed (``refusal``);
- **a duration** is held to the music: at most the end of the last song the class plays
  (``music.songs``, whichever ends last), else the map's length, so the song lanes always cover
  the class; the plan's own length gate runs on the fit as always.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from classbeat.agent.studio_mix import StudioTrack
from classbeat.classes.spotify_edit_model import SpotifyDraft
from classbeat.classes.user_edit_model import (
    AnchorBlock,
    Edit,
    PinClear,
    PinConfirm,
    PinSet,
    RemoveAnchor,
)
from classbeat.studio.mix_choice_model import Proposal
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.swedebeat_studio.studio_edit_calc import EditState

AUDIO_EDIT_UNAVAILABLE: Final = "audio_edit_unavailable"


@dataclass(frozen=True)
class NoTracks:
    """``mix_apply_calc.StudioTracks`` of a draft with no studio track."""

    @property
    def references(self) -> Sequence[str]:
        return ()

    @property
    def tracks(self) -> Sequence[StudioTrack]:
        return ()

    def facts(self) -> Mapping[str, TrackFacts]:
        return {}


def refusal(edit: Edit) -> str | None:
    """``audio_edit_unavailable`` for an edit that needs a studio piece, else ``None``."""
    if isinstance(edit, PinSet | PinConfirm | PinClear | AnchorBlock | RemoveAnchor):
        return AUDIO_EDIT_UNAVAILABLE
    return None


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _all(items: object, *keys: str) -> bool:
    """Whether ``items`` is a list of mappings that each hold a finite number under every key."""
    return isinstance(items, list) and all(
        isinstance(item, dict) and all(_number(item.get(key)) for key in keys)
        for item in cast(list[Any], items)
    )


def readable(edit_map: Mapping[str, Any]) -> bool:
    """Whether the stored map has the shape ``agent.design.check`` reads: a version of 1 or more,
    a length and a crossfade, songs with their times and tempo, and tracks with their length. A
    map that fails it was not made by the library, so the edit answers ``edit_unavailable`` here
    instead of a library error surfacing as something else (``check`` is then not wrapped)."""
    version = edit_map.get("version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 1
        and _number(edit_map.get("duration"))
        and _number(edit_map.get("crossfade"))
        and bool(edit_map.get("songs"))
        and _all(edit_map.get("songs"), "start", "end", "bpm", "song", "track")
        and _all(edit_map.get("tracks"), "sec")
        and _all(edit_map.get("events", []), "t")
    )


def _total_cap(draft: SpotifyDraft) -> int | None:
    songs = draft.music.songs
    end = max(s.end for s in songs) if songs else draft.edit_map.get("duration")
    if isinstance(end, bool) or not isinstance(end, int | float) or not math.isfinite(end):
        return None
    return math.floor(end)


def state_of(draft: SpotifyDraft) -> EditState | None:
    """The state of ``draft``, ``None`` when its map has no version or length to read (the caller
    answers ``edit_unavailable``: it is not a map the library made)."""
    cap = _total_cap(draft)
    if not readable(draft.edit_map) or cap is None:
        return None
    version = int(draft.edit_map["version"])
    return EditState(
        content=draft.content,
        spec={"tracks": []},
        overlay=draft.overlay,
        music=NoTracks(),
        analysed={},
        proposal=Proposal(approved=(), spec={"tracks": []}, target_sec=0),
        choices=None,
        map_version=version,
        fixed_map=draft.edit_map,
        max_total_sec=cap,
    )

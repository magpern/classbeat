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
  (``music.songs``), else the map's length, so the song lanes always cover the class; the plan's
  own length gate runs on the fit as always.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from classbeat.agent.studio_mix import StudioTrack
from classbeat.classes.spotify_edit_draft import SpotifyDraft
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


def _total_cap(draft: SpotifyDraft) -> int | None:
    songs = draft.music.songs
    end = songs[-1].end if songs else draft.edit_map.get("duration")
    if isinstance(end, bool) or not isinstance(end, int | float) or not math.isfinite(end):
        return None
    return math.floor(end)


def state_of(draft: SpotifyDraft) -> EditState | None:
    """The state of ``draft``, ``None`` when its map has no version or length to read (the caller
    answers ``edit_unavailable``: it is not a map the library made)."""
    version = draft.edit_map.get("version")
    cap = _total_cap(draft)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or cap is None:
        return None
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

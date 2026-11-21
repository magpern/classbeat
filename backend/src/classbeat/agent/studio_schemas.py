"""What the studio stages take from the model (ADR 0036 section 4.4, SR C5): the workout **and**
the mix plan, by track index only.

A track is named by its **index** in the approved list (0-based), never by a catalogue reference,
and the model writes no length, BPM or total: each track's length is its analysis's, and the
total is the backend's (the one duration rule). The schemas forbid every other field, so a
``reference``, ``length`` or ``tempo`` in a call is refused before any handler runs. The rules of
the plan itself (the order, the cuts, the crossfades) are ``studio.mix_plan_policy``'s, each with a
named reason the model gets back.
"""

from pydantic import BaseModel, Field

from classbeat.agent.schemas import STRICT, Note, WorkoutDraft
from classbeat.studio.mix_plan_policy import MAX_TRACKS

SCHEMA_VERSION = 1  # of the schemas below


class MixTrackInput(BaseModel):
    model_config = STRICT

    track: int = Field(ge=0, lt=MAX_TRACKS, description="The track's index in the music summary.")
    start_sec: float = Field(description="Where the piece starts playing, in the track's seconds.")
    end_sec: float = Field(description="Where it stops, in its own seconds (its length: the end).")


class MixInput(BaseModel):
    """The mix: its pieces in the order they play (every approved track at least once; a track may
    play again in a later piece, never in the next one), and one crossfade per change."""

    model_config = STRICT

    tracks: list[MixTrackInput] = Field(min_length=1, max_length=MAX_TRACKS)
    crossfades: list[float] = Field(
        max_length=MAX_TRACKS - 1,
        description="Whole seconds, 0 to 30, between piece n and n+1 (one fewer than pieces).",
    )


class StudioDraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft`` of the studio ``design`` stage."""

    model_config = STRICT

    draft: WorkoutDraft
    mix: MixInput


class StudioReviseInput(StudioDraftInput):
    """``submit_draft`` of the studio ``revise`` stage: the draft, the mix and an optional note
    (shown to the user as plain text, never sent back to a model)."""

    note: Note = Field(default="", description="One sentence on what you changed (optional).")

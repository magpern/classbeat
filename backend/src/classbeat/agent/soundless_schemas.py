"""The soundless schemas (ADR 0007 amendment of 2026-11-03, item 4; #527), split out of
``classbeat.agent.schemas`` (#1493): ``Interval`` with the cadence a range only, so the music
cadence is neither offered nor accepted (the strict schema refuses it).

The classes carry comments, not docstrings, where a docstring would become the schema's
description, which the model reads, and a soundless model is never told about music.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from classbeat.agent.schemas import (
    STRICT,
    Cue,
    Description,
    Game,
    Note,
    Rpm,
    SectionTag,
    SectionTagged,
    SegmentName,
    WorkoutDraft,
    WorkoutName,
    ordered_rpm,
)


class SoundlessInterval(BaseModel):
    model_config = STRICT

    sec: int = Field(ge=1, le=3600, description="Length in whole seconds.")
    zone: int = Field(ge=1, le=7, description="Effort zone 1 (recovery) to 7 (all out).")
    rpm: Annotated[Rpm, AfterValidator(ordered_rpm)] = Field(description="Cadence as [from, to].")
    standing: bool
    game: Game | None = None
    ftp_test: Literal[3, 5, 20] | None = Field(
        default=None, description="An FTP test of 3, 5 or 20 minutes; sec must match."
    )
    cue: Cue = ""


class SoundlessSegment(SectionTagged):
    model_config = STRICT

    name: SegmentName
    kind: Literal["warmup", "main", "recovery", "cooldown"]
    intervals: list[SoundlessInterval] = Field(min_length=1, max_length=60)
    section: SectionTag = None


# A soundless class as the model hands it back: exactly the fields of a ``WorkoutDraft``, the
# cadence a range only, so ``workout_draft`` gives the ``WorkoutDraft`` that is checked and stored
# (Save and the class page read it unchanged).
class SoundlessDraft(BaseModel):
    model_config = STRICT

    name: WorkoutName
    description: Description = ""
    segments: list[SoundlessSegment] = Field(min_length=1, max_length=20)

    def workout_draft(self) -> WorkoutDraft:
        return WorkoutDraft.model_validate(self.model_dump(mode="python"))


class SoundlessDraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft`` of a class: the schema of its draft."""

    model_config = STRICT

    draft: SoundlessDraft


class SoundlessReviseInput(SoundlessDraftInput):
    """``submit_draft`` of a soundless ``revise``: the draft and the optional note, as
    ``ReviseInput`` (shown to the user as plain text, never sent back to a model)."""

    note: Note = Field(default="", description="One sentence on what you changed (optional).")

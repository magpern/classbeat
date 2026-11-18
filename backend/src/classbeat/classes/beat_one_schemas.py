"""The beat-one correction models of the class API (ADR 0007 amendment "beat-one corrections"),
split out of ``classbeat.classes.schemas`` (CLAUDE.md, split on touch), which re-exports them."""

from dataclasses import asdict
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BeatOneSongOut(BaseModel):
    """A song the current revision plays, with the class's beat-one correction for it (ADR 0007
    amendment "beat-one corrections"): ``beats`` set by the user, ``applied`` in the revision."""

    song: int
    start: float
    end: float
    bpm: float
    estimate: bool
    title: str | None = None
    beats: int
    applied: int
    bpm_changed: bool = False


class BeatOneStaleOut(BaseModel):
    """A row for other music or for a song the current revision does not play: cleared by id."""

    id: UUID
    song: int
    beats: int


class BeatOneOut(BaseModel):
    """``available`` only when the current revision has beat data (``music.songs``) and the class
    has music to fit to (not soundless, not without a music source); ``revision_id`` is the
    current revision; ``pending`` when the corrections differ from what it applied; ``stale``
    lists the rows for other music and for songs it does not play."""

    available: bool
    revision_id: UUID | None = None
    songs: list[BeatOneSongOut] = []
    pending: bool = False
    stale: list[BeatOneStaleOut] = []

    @classmethod
    def of(cls, view: Any) -> "BeatOneOut":
        return cls(
            available=view.available,
            revision_id=view.revision_id,
            songs=[BeatOneSongOut(**asdict(s)) for s in view.songs],
            pending=view.pending,
            stale=[BeatOneStaleOut(**asdict(r)) for r in view.stale],
        )


class BeatOneIn(BaseModel):
    """``PUT /api/classes/{id}/beat-one/{song}``: the total shift and the revision it is for."""

    model_config = ConfigDict(extra="forbid")

    beats: int = Field(ge=-3, le=3, strict=True)  # a JSON integer: never 1.0 or "1"
    revision_id: UUID

    @model_validator(mode="after")
    def _not_zero(self) -> Self:
        if self.beats == 0:
            raise ValueError("clear a correction with DELETE")
        return self

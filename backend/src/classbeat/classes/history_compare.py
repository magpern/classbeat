"""The answer models of the comparison of two revisions (``classbeat.classes.history``)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class WarningsOut(BaseModel):
    unknown_bpm: int
    unsure_beat_one: int


class SegmentsChangeOut(BaseModel):
    added: int
    removed: int


class IntervalsChangeOut(BaseModel):
    added: int
    removed: int
    changed: int


class BeforeAfterOut(BaseModel):
    before: float
    after: float


class MusicChangeOut(BaseModel):
    same_source: bool
    same_fingerprint: bool


class GameChangeOut(BaseModel):
    segment: int
    before: str | None  # a game id, ``None`` when a game was added (a retired one may show)
    after: str | None  # a game id, ``None`` when the game was removed


class CompareOut(BaseModel):
    from_id: UUID
    from_number: int
    to_id: UUID
    to_number: int
    segments: SegmentsChangeOut
    intervals: IntervalsChangeOut
    duration_sec: BeforeAfterOut
    tss: BeforeAfterOut
    text_changed: bool
    warnings: dict[Literal["from", "to"], WarningsOut]
    music: MusicChangeOut
    # Games that appeared, went or changed type (``design.diff``); ``segment`` counts from 1.
    game_changes: list[GameChangeOut] = []

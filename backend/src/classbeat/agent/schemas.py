"""Strict schemas of what the model may hand back (ADR 0006 section 4).

Every model here forbids extra fields and is strict (no coercion); their JSON schemas are the
tools' ``input_schema``s. The dispatcher validates every tool input against them from JSON
(``model_validate_json``), so a list is a list and an int an int, whatever the provider parsed.

``WorkoutDraft.to_spec()`` gives the ``mowl-workouts`` spec dict; ``workout.validate()`` stays the
authority on the MOWL app's own rules (cooldown, games, FTP tests, three hours). Text fields
written by the model have their control and format characters removed on validation, so what is
stored never carries them.

``Brief`` is the brief of the new-class form as the model may see it (section 7): the guided
options and the user's free text, nothing else.
"""

import unicodedata
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

STRICT = ConfigDict(extra="forbid", strict=True)


def clean_text(value: str) -> str:
    """``value`` without control (Cc) and format (Cf: bidi overrides, zero-width) characters,
    tabs and newlines kept as spaces, trimmed."""
    out = []
    for char in value:
        if char in "\t\n\r":
            out.append(" ")
        elif unicodedata.category(char) not in ("Cc", "Cf"):
            out.append(char)
    return "".join(out).strip()


def _required_text(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("must have visible text")
    return cleaned


def _bounded(max_length: int, *, required: bool) -> Any:
    return Field(min_length=1 if required else 0, max_length=max_length)


SegmentName = Annotated[str, _bounded(60, required=True), AfterValidator(_required_text)]
WorkoutName = Annotated[str, _bounded(100, required=True), AfterValidator(_required_text)]
Description = Annotated[str, _bounded(500, required=False), AfterValidator(clean_text)]
Cue = Annotated[str, _bounded(120, required=False), AfterValidator(clean_text)]
Style = Annotated[str, _bounded(60, required=False), AfterValidator(clean_text)]
BriefText = Annotated[str, _bounded(2000, required=False), AfterValidator(clean_text)]

# The library's GAMES (a test checks they match).
Game = Literal["racetrack", "power_pull", "fast_lane", "overflow", "ghost_target"]
Rpm = Annotated[list[Annotated[int, Field(ge=0, le=200)]], Field(min_length=2, max_length=2)]


def _ordered(rpm: list[int] | str) -> list[int] | str:
    if isinstance(rpm, list) and rpm[0] > rpm[1]:
        raise ValueError("rpm must be [from, to] with from <= to")
    return rpm


class Interval(BaseModel):
    model_config = STRICT

    sec: int = Field(ge=1, le=3600, description="Length in whole seconds.")
    zone: int = Field(ge=1, le=7, description="Effort zone 1 (recovery) to 7 (all out).")
    rpm: Annotated[Rpm | Literal["beat"], AfterValidator(_ordered)] = Field(
        description='Cadence as [from, to], or "beat" to ride on the beat of the song under it.'
    )
    standing: bool
    game: Game | None = None
    ftp_test: Literal[3, 5, 20] | None = Field(
        default=None, description="An FTP test of 3, 5 or 20 minutes; sec must match."
    )
    cue: Cue = ""

    def to_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "sec": self.sec,
            "zone": self.zone,
            "rpm": self.rpm if self.rpm == "beat" else list(self.rpm),
            "standing": self.standing,
        }
        if self.game is not None:
            spec["game"] = self.game
        if self.ftp_test is not None:
            spec["ftp_test"] = self.ftp_test
        return spec


class Segment(BaseModel):
    model_config = STRICT

    name: SegmentName
    kind: Literal["warmup", "main", "recovery", "cooldown"]
    intervals: list[Interval] = Field(min_length=1, max_length=60)


class WorkoutDraft(BaseModel):
    model_config = STRICT

    name: WorkoutName
    description: Description = ""
    segments: list[Segment] = Field(min_length=1, max_length=20)

    def to_spec(self) -> dict[str, Any]:
        """The library's spec dict (cues are classbeat's own and not part of it)."""
        return {
            "name": self.name,
            "description": self.description,
            "segments": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "intervals": [i.to_spec() for i in s.intervals],
                }
                for s in self.segments
            ],
        }


class DraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft``."""

    model_config = STRICT

    draft: WorkoutDraft


class MusicInput(BaseModel):
    """``get_music``: without ``track`` the music summary; with it, that track's sections."""

    model_config = STRICT

    track: int | None = Field(
        default=None, description="A track's index in the music summary, for its sections."
    )


class Brief(BaseModel):
    """The new-class form's brief (ADR 0006 section 7)."""

    model_config = STRICT

    length_min: int = Field(ge=10, le=180)
    intensity: Literal["easy", "moderate", "hard"] = "moderate"
    climbs: int = Field(default=0, ge=0, le=10)
    sprints: int = Field(default=0, ge=0, le=20)
    races: int = Field(default=0, ge=0, le=5)
    ftp_test: Literal[3, 5, 20] | None = None
    style: Style = ""
    text: BriefText = ""

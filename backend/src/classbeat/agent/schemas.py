"""Strict schemas of what the model may hand back (ADR 0006 section 4).

Every model here forbids extra fields and is strict (no coercion); their JSON schemas are the
tools' ``input_schema``s. The dispatcher validates every tool input against them from JSON
(``model_validate_json``), so a list is a list and an int an int, whatever the provider parsed.

``WorkoutDraft.to_spec()`` gives the ``mowl-workouts`` spec dict; ``workout.validate()`` stays the
authority on the MOWL app's own rules (cooldown, games, FTP tests, three hours). Text fields
written by the model have their control and format characters removed on validation, so what is
stored never carries them.

The brief of the new-class form (``Brief``) is ``classbeat.agent.brief``.
"""

import unicodedata
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

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
Note = Annotated[str, _bounded(300, required=False), AfterValidator(clean_text)]

# The four MOWL games (ADR 0029 section 3), a subset of the library's GAMES (a test checks it).
Game = Literal["racetrack", "power_pull", "fast_lane", "overflow"]
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


class RunningInterval(BaseModel):
    """A running interval (ADR 0018, mowl-workouts 0.9.0 ``validate_running``): its length, a zone
    of 1 to 5 and the treadmill incline. No cadence, position, game or FTP test exists here, and
    a strict schema refuses any of them."""

    model_config = STRICT

    sec: int = Field(ge=1, le=3600, description="Length in whole seconds.")
    zone: int = Field(ge=1, le=5, description="Effort zone 1 (recovery) to 5 (VO2max).")
    gradient: int = Field(
        default=0,
        ge=0,
        le=1,
        description="Treadmill incline: 0 flat, 1 the raised incline of MOWL's running classes.",
    )
    cue: Cue = ""

    def to_spec(self) -> dict[str, Any]:
        return {"sec": self.sec, "zone": self.zone, "gradient": self.gradient}


class RunningSegment(BaseModel):
    model_config = STRICT

    name: SegmentName
    kind: Literal["warmup", "main", "recovery", "cooldown"]
    intervals: list[RunningInterval] = Field(min_length=1, max_length=60)


class RunningDraft(BaseModel):
    """A running class as the model hands it back (ADR 0018 section 2). ``to_spec`` adds the
    library's ``"sport": "running"``; the model never states the sport."""

    model_config = STRICT

    name: WorkoutName
    description: Description = ""
    segments: list[RunningSegment] = Field(min_length=1, max_length=20)

    def to_spec(self) -> dict[str, Any]:
        return {
            "sport": "running",
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


# The soundless schemas (ADR 0007 amendment of 2026-11-03, item 4; #527): ``Interval`` with the
# cadence a range only, so the music cadence is neither offered nor accepted (the strict schema
# refuses it). Comments, not docstrings: a docstring becomes the schema's description, which the
# model reads, and a soundless model is never told about music.
class SoundlessInterval(BaseModel):
    model_config = STRICT

    sec: int = Field(ge=1, le=3600, description="Length in whole seconds.")
    zone: int = Field(ge=1, le=7, description="Effort zone 1 (recovery) to 7 (all out).")
    rpm: Annotated[Rpm, AfterValidator(_ordered)] = Field(description="Cadence as [from, to].")
    standing: bool
    game: Game | None = None
    ftp_test: Literal[3, 5, 20] | None = Field(
        default=None, description="An FTP test of 3, 5 or 20 minutes; sec must match."
    )
    cue: Cue = ""


class SoundlessSegment(BaseModel):
    model_config = STRICT

    name: SegmentName
    kind: Literal["warmup", "main", "recovery", "cooldown"]
    intervals: list[SoundlessInterval] = Field(min_length=1, max_length=60)


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


AnyDraft = WorkoutDraft | RunningDraft


def draft_model(sport: str | None) -> type[WorkoutDraft] | type[RunningDraft]:
    """The **strict** draft schema of a class's sport: ``running``, else cycling (absent is
    cycling). What is saved or handed to a model; a historical record is read with
    ``classbeat.agent.stored_draft``."""
    return RunningDraft if sport == "running" else WorkoutDraft


class DraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft``."""

    model_config = STRICT

    draft: WorkoutDraft


class ReviseInput(DraftInput):
    """``submit_draft`` of the ``revise`` stage: the draft and, optionally, one sentence on what
    changed. The note is shown to the user as plain text and never sent back to a model."""

    note: Note = Field(default="", description="One sentence on what you changed (optional).")


class RunningDraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft`` of a running class: the running schema only."""

    model_config = STRICT

    draft: RunningDraft


class RunningReviseInput(RunningDraftInput):
    """``submit_draft`` of a running ``revise``: the draft and the optional note, as
    ``ReviseInput``."""

    note: Note = Field(default="", description="One sentence on what you changed (optional).")


class SoundlessDraftInput(BaseModel):
    """``preview_draft`` and ``submit_draft`` of a class: the schema of its draft."""

    model_config = STRICT

    draft: SoundlessDraft


class SoundlessReviseInput(SoundlessDraftInput):
    """``submit_draft`` of a soundless ``revise``: the draft and the optional note, as
    ``ReviseInput`` (shown to the user as plain text, never sent back to a model)."""

    note: Note = Field(default="", description="One sentence on what you changed (optional).")


class SongsRequestedInput(BaseModel):
    """``songs_requested`` of the ``revise`` stage: the request asks for other songs and for
    nothing else, so there is nothing to revise. It takes no data."""

    model_config = STRICT


class MusicInput(BaseModel):
    """``get_music``: without ``track`` the music summary; with it, that track's sections."""

    model_config = STRICT

    track: int | None = Field(
        default=None, description="A track's index in the music summary, for its sections."
    )


# --- song picking (ADR 0008 section 3) ----------------------------------------------------------

SONG_SCHEMA_VERSION = 1  # of the schemas below; recorded in a song_list revision's provenance

Role = Literal["warmup", "build", "climb", "sprint", "interval", "race", "recovery", "cooldown"]
ROLES: tuple[str, ...] = (
    "warmup", "build", "climb", "sprint", "interval", "race", "recovery", "cooldown",
)  # fmt: skip
MAX_POOL = 120
MAX_SONGS = 40
TrackText = Annotated[str, Field(max_length=120)]


SEARCH_DROPPED = str.maketrans({c: " " for c in ':"\\'})  # Spotify's field-filter and quote syntax


def _search_text(value: str) -> str:
    """Control and format characters removed (``clean_text``), and the characters that would turn
    the text into a Spotify field filter (``artist:``, ``year:``, ``tag:new``) or a quoted phrase
    replaced by spaces: the model supplies words, never the query syntax."""
    words = clean_text(value).translate(SEARCH_DROPPED).split()
    if not words:
        raise ValueError("must have visible words")
    return " ".join(words)


class SearchInput(BaseModel):
    """``search_tracks``: text and years only. The backend adds ``year:`` and builds the request;
    the model supplies no field filter, URL, market or limit."""

    model_config = STRICT

    query: Annotated[str, _bounded(100, required=True), AfterValidator(_search_text)] = Field(
        description="Words to search for (a title, an artist, a style). Plain words only."
    )
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def _years(self) -> Self:
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not be after year_to")
        return self


class PoolEntry(BaseModel):
    model_config = STRICT

    ref: int = Field(ge=0, description="A ref from a search_tracks result.")
    role: Role


class SongPool(BaseModel):
    """``check_pool`` and ``submit_pool``: candidates as ``ref`` (an index into the job's search
    registry) and the role each could play. The count is checked against the brief by the tool."""

    model_config = STRICT

    entries: list[PoolEntry] = Field(min_length=1, max_length=MAX_POOL)


class SongRef(BaseModel):
    model_config = STRICT

    source: Literal["pool", "kept"]
    index: int = Field(ge=0, description="An index from get_pool (pool) or the kept songs (kept).")
    role: Role


class SongList(BaseModel):
    """``check_song_list`` and ``submit_song_list``: the chosen songs in play order and an
    optional note. Only indexes and roles: titles, lengths and BPM are the backend's."""

    model_config = STRICT

    songs: list[SongRef] = Field(min_length=1, max_length=MAX_SONGS)
    note: Note = Field(default="", description="One short sentence for the instructor (optional).")


class GetPoolInput(BaseModel):
    """``get_pool`` takes no arguments."""

    model_config = STRICT

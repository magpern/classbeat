"""The input of ``design_with_songs`` (ADR 0008 section 4).

``{class_id | title, brief, mode: "pick" | "resume", resume?: {song_list_revision_id},
seed?: {song_list_revision_id, replace: [int]}, mowl_connection_id, spotify_connection_id}``.

- ``CONNECTIONS = ("mowl", "spotify")``: the jobs API refuses the job, and later its approval,
  unless both ids are the user's current connections (and, at approval, ``connected``).
- ``brief``: the new-class form's, refused (422) when it would not fit ``classes.brief``, as for
  ``design_draft``.
- ``seed`` ("Ask for changes", section 5): a pick that keeps the seed list's other songs in their
  slots and replaces the ones at ``replace`` (positions in that list, each once). Only with
  ``mode = "pick"`` and an existing class (``class_id``); ``admit`` checks the rest.
- ``resume`` names an approved list to start phase 2 for (section 6): exactly when
  ``mode = "resume"``, and only with an existing class; ``admit`` checks the receipt.

No field is a server field, and nothing in the input reaches a Spotify or MOWL request: the pool,
the playlists and every id written come from the user's own rows.
"""

from typing import Annotated, ClassVar, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.schemas import MAX_SONGS, Brief
from classbeat.design import revision
from classbeat.design.revise import Request, Sha256Hex
from classbeat.jobs.inputs import JobInput, jsonb_size

Slot = Annotated[int, Field(ge=0, lt=MAX_SONGS)]
Slots = Annotated[list[Slot], Field(max_length=MAX_SONGS)]


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID
    replace: list[Slot] = Field(min_length=1, max_length=MAX_SONGS)

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        if len(set(self.replace)) != len(self.replace):
            raise ValueError("a slot is replaced once")
        return self


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID


class DesignWithSongsInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl", "spotify")

    brief: Brief
    mode: Literal["pick", "resume"] = "pick"
    resume: Resume | None = None
    seed: Seed | None = None
    mowl_connection_id: UUID
    spotify_connection_id: UUID

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if jsonb_size(self.brief.model_dump(mode="json")) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the brief is too long")
        if (self.mode == "resume") != (self.resume is not None):
            raise ValueError("resume names its list exactly in mode resume")
        if self.resume is not None and self.class_id is None:
            raise ValueError("a resume belongs to an existing class")
        if self.seed is not None and (self.mode != "pick" or self.class_id is None):
            raise ValueError("a seed belongs to a pick of an existing class")
        return self


class ReviseSongsInput(JobInput):
    """``revise_songs`` (section 6, "Change songs"): ``{class_id, base_revision_id, mode,
    replace, request}`` for a pick, ``{class_id, base_revision_id, mode: "resume", resume}`` for a
    resume, plus both connection ids.

    - ``replace``: positions in the base draft's picked songs (``music.song_list_id``), each once,
      or ``"all"`` (no song kept); ``request``: what the user wants (1 to 2000 visible
      characters). Both are required in mode ``pick`` and refused in mode ``resume``: a resume
      runs phase 2 for the list already approved, with the request that list was made for.
    - Server fields, set only by ``admit``: ``base_revision_hash`` (the base's hash at creation)
      and ``request_message_id`` (the user's message row: inserted with a pick, the approved
      job's own for a resume).
    """

    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl", "spotify")

    base_revision_id: UUID
    mode: Literal["pick", "resume"] = "pick"
    replace: Slots | Literal["all"] | None = None
    request: Request | None = None
    resume: Resume | None = None
    mowl_connection_id: UUID
    spotify_connection_id: UUID
    base_revision_hash: Sha256Hex | None = None
    request_message_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.class_id is None:
            raise ValueError("a song change belongs to an existing class")
        if self.mode == "resume":
            if self.resume is None or self.replace is not None or self.request is not None:
                raise ValueError("a resume names its list and nothing to change")
            return self
        if self.resume is not None or self.replace is None or self.request is None:
            raise ValueError("a pick names the songs to replace and the request")
        if isinstance(self.replace, list) and (
            not self.replace or len(set(self.replace)) != len(self.replace)
        ):
            raise ValueError("replace names at least one slot, each once")
        return self


class SongJob(Protocol):
    """What phases 1 and 2 read from either kind's input."""

    @property
    def mowl_connection_id(self) -> UUID: ...
    @property
    def spotify_connection_id(self) -> UUID: ...
    @property
    def resume(self) -> Resume | None: ...

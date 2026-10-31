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

from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.schemas import MAX_SONGS, Brief
from classbeat.design import revision
from classbeat.jobs.inputs import JobInput, jsonb_size

Slot = Annotated[int, Field(ge=0, lt=MAX_SONGS)]


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

"""The input of ``design_with_songs`` (ADR 0008 section 4).

``{class_id | title, brief, mode: "pick" | "resume", resume?: {song_list_revision_id},
seed?: {song_list_revision_id, replace: [int]}, source?: {source: "mowl_spotify_playlist",
spotify_playlist_id}, mowl_connection_id, spotify_connection_id}``.

- ``CONNECTIONS = ("mowl", "spotify")``: the jobs API refuses the job, and later its approval,
  unless both ids are the user's current connections (and, at approval, ``connected``).
- ``brief``: the new-class form's, refused (422) when it would not fit ``classes.brief``, as for
  ``design_draft``.
- ``seed`` ("Ask for changes", section 5): a pick that keeps the seed list's other songs in their
  slots and replaces the ones at ``replace`` (positions in that list, each once). Only with
  ``mode = "pick"`` and an existing class (``class_id``); ``admit`` checks the rest.
- ``source`` (roadmap Amendment 26, ADR 0008 amendment of 2026-11-16): the user's own Spotify
  playlist, as ``design_draft`` takes it. Phase 1 then makes the ``song_list`` from that playlist's
  analysed tracks in their order, with no model, no search and no pool
  (``classbeat.songs.from_playlist``); the approval and phase 2 are the pick's. The playlist is
  only read (through MOWL); phase 2 writes a **new** private playlist. Only with ``mode = "pick"``
  and no ``seed``.
- ``resume`` names an approved list to start phase 2 for (section 6): exactly when
  ``mode = "resume"``, and only with an existing class; ``admit`` checks the receipt.
- **The composer's modes** (ADR 0038 sections 5 and 6, #1188): ``fill`` puts songs into the
  class's current sketch, ``swap`` replaces songs of the waiting list (``seed``), ``change``
  keeps its songs and designs again with ``request``. Each names the sketch it started from as
  ``base_revision_id``; ``swap`` and ``change`` also the waiting job they replace
  (``replaces_job_id``) and the list it waits on (``seed``; a change keeps every song, so its
  ``replace`` is empty). ``sources`` (the attached Spotify playlists) and ``pinned`` (the reading's
  pinned songs) come from the class's stored request. All of these are **server fields**: only
  the hand-offs (``classbeat.classes.song_fill``, ``song_swap``, the pending submit) set them, with
  no credit, so ``POST /api/jobs`` can never create a composer mode.

The composer's fields (``COMPOSER_FIELDS``) are server fields; the others are not. Nothing in the
input reaches a Spotify or MOWL request: the pool, the playlists and every id written come from
the user's own rows.
"""

from typing import Annotated, ClassVar, Final, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.brief import Brief
from classbeat.agent.schemas import MAX_SONGS
from classbeat.design import revision
from classbeat.design.interpret_composer_policy import PinnedList
from classbeat.design.kind import SpotifyMusicChoice
from classbeat.design.music_sources import MusicSources
from classbeat.design.revise import Request, Sha256Hex
from classbeat.jobs.inputs import ModelJobInput, jsonb_size

Slot = Annotated[int, Field(ge=0, lt=MAX_SONGS)]
Slots = Annotated[list[Slot], Field(max_length=MAX_SONGS)]


Mode = Literal["pick", "resume", "fill", "swap", "change"]
COMPOSER_MODES: Final = ("fill", "swap", "change")
REPLACING_MODES: Final = ("swap", "change")
COMPOSER_FIELDS: Final = frozenset(
    {"base_revision_id", "replaces_job_id", "sources", "pinned", "request", "request_message_id"}
)


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID
    replace: list[Slot] = Field(max_length=MAX_SONGS)  # empty only for a change (``_composer``)

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        if len(set(self.replace)) != len(self.replace):
            raise ValueError("a slot is replaced once")
        return self


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID


class DesignWithSongsInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl", "spotify")

    brief: Brief
    mode: Mode = "pick"
    resume: Resume | None = None
    seed: Seed | None = None
    source: SpotifyMusicChoice | None = None
    mowl_connection_id: UUID
    spotify_connection_id: UUID
    # The composer's modes (module docstring); server fields, set by the hand-offs only.
    base_revision_id: UUID | None = None
    replaces_job_id: UUID | None = None
    sources: MusicSources | None = None
    pinned: PinnedList | None = None
    request: Request | None = None
    request_message_id: UUID | None = None

    @property
    def composer(self) -> bool:
        return self.mode in COMPOSER_MODES

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if jsonb_size(self.brief.stored()) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the brief is too long")
        if self.brief.sport != "cycling":  # ADR 0018: running v1 has no song picking
            raise ValueError("song picking is for cycling classes only")
        if (self.mode == "resume") != (self.resume is not None):
            raise ValueError("resume names its list exactly in mode resume")
        if self.resume is not None and self.class_id is None:
            raise ValueError("a resume belongs to an existing class")
        seeded = ("pick", *REPLACING_MODES)
        if self.seed is not None and (self.mode not in seeded or self.class_id is None):
            raise ValueError("a seed belongs to a pick, swap or change of an existing class")
        if self.source is not None and (self.mode != "pick" or self.seed is not None):
            raise ValueError("a source playlist belongs to a pick without a seed")
        return self._composer()

    def _composer(self) -> Self:
        composer = self.mode in COMPOSER_MODES
        if not composer:
            if any(getattr(self, name) is not None for name in COMPOSER_FIELDS):
                raise ValueError("these fields belong to the composer's modes")
            if self.seed is not None and not self.seed.replace:
                raise ValueError("a seed replaces at least one slot")
            return self
        if self.class_id is None or self.base_revision_id is None or self.source is not None:
            raise ValueError("a composer job names its class and its sketch")
        replacing = self.mode in REPLACING_MODES
        if replacing != (self.replaces_job_id is not None) or replacing != (self.seed is not None):
            raise ValueError("a swap or change names the waiting job and its list")
        if self.seed is not None and (self.mode == "change") == bool(self.seed.replace):
            raise ValueError("a swap replaces slots; a change keeps every song")
        if (self.mode == "change") != (self.request is not None):
            raise ValueError("a change, and only a change, carries the request")
        if (self.request is None) != (self.request_message_id is None):
            raise ValueError("a request comes with its message")
        return self


class ReviseSongsInput(ModelJobInput):
    """``revise_songs`` (section 6, "Change songs"): ``{class_id, base_revision_id, mode,
    replace, request}`` for a pick, ``{class_id, base_revision_id, mode: "resume", resume}`` for a
    resume, plus both connection ids.

    - ``replace``: positions in the base draft's picked songs (``music.song_list_id``), each once,
      or ``"all"`` (no song kept); ``request``: what the user wants (1 to 2000 visible
      characters). Both are required in mode ``pick`` and refused in mode ``resume``: a resume
      runs phase 2 for the list already approved, with the request that list was made for.
    - ``match_revision_id`` (ADR 0016 section 6): a catalogue replace. A pick names that match
      and neither ``replace`` nor ``request``; the server fills the slots and the status-only
      fact. A resume never names it.
    - Server fields, set only by ``admit``: ``base_revision_hash`` (the base's hash at creation)
      and ``request_message_id`` (the user's message row: inserted with a pick, the approved
      job's own for a resume). A catalogue replace also sets ``catalogue_replace``,
      ``catalogue_fact`` and ``swedebeat_connection_id``; a resume of that job copies them.
    """

    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl", "spotify")

    base_revision_id: UUID
    mode: Literal["pick", "resume"] = "pick"
    replace: Slots | Literal["all"] | None = None
    request: Request | None = None
    resume: Resume | None = None
    match_revision_id: UUID | None = None
    mowl_connection_id: UUID
    spotify_connection_id: UUID
    base_revision_hash: Sha256Hex | None = None
    request_message_id: UUID | None = None
    catalogue_replace: Slots | None = None
    catalogue_fact: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    swedebeat_connection_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.class_id is None:
            raise ValueError("a song change belongs to an existing class")
        catalogue = (
            self.catalogue_replace,
            self.catalogue_fact,
            self.swedebeat_connection_id,
        )
        if any(part is not None for part in catalogue) and any(part is None for part in catalogue):
            raise ValueError("a catalogue replace is stored complete or not at all")
        if self.catalogue_replace is not None and (
            not self.catalogue_replace
            or len(set(self.catalogue_replace)) != len(self.catalogue_replace)
        ):
            raise ValueError("catalogue replace names each slot once")
        if self.mode == "resume":
            if self.resume is None or self.replace is not None or self.request is not None:
                raise ValueError("a resume names its list and nothing to change")
            if self.match_revision_id is not None:
                raise ValueError("a resume does not name a match")
            return self
        if self.match_revision_id is not None:
            if self.resume is not None or self.replace is not None or self.request is not None:
                raise ValueError("a catalogue replace names the match, not the slots or the words")
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

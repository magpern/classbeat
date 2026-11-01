"""Response models of the class API. Nothing here names a MOWL or Spotify id, a link id, a
fingerprint, an agent run id, a hash or lease data (ADR 0007 section 12)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from classbeat.agent.schemas import Brief


class OpenJobOut(BaseModel):
    id: UUID
    kind: str
    state: str


class SavedOut(BaseModel):
    """Summary of the live (not detached) MOWL workouts classbeat made for the class.

    ``latest_saved_workout_id`` is classbeat's own id of the newest live ``saved_workouts`` row,
    the one Update names (ADR 0007 section 5), never a MOWL program id; ``None`` when the class
    has no live row or that row is not one Update would accept (its creating operation is of
    another class). ``saved_state`` says which: ``none`` (no live row), ``ok`` or
    ``inconsistent`` (a live row exists but the id is withheld, so the page can say the saved
    state needs attention instead of showing a missing id). ``detached_count`` counts the rows
    detached by a MOWL disconnect, reconnect or ``keys wipe`` (section 9)."""

    count: int
    latest_revision_number: int | None
    latest_saved_at: datetime | None
    latest_up_to_date: bool | None
    latest_saved_workout_id: UUID | None
    saved_state: Literal["none", "ok", "inconsistent"]
    detached_count: int


class ClassOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    current_revision_id: UUID | None
    current_revision_number: int | None
    revision_count: int
    saved: SavedOut
    open_job_id: UUID | None
    open_job: OpenJobOut | None
    # The current revision's intervals as [zone, seconds], in order: enough for a mini profile
    # and a length, nothing else (ADR 0013 section 11, UI2). Empty without a current revision.
    profile: list[tuple[int, int]] = []
    # ADR 0012 section 7: the current revision has a zone 7 interval, so its TSS is approximate.
    tss_approximate: bool = False

    @classmethod
    def of(cls, row: Any) -> "ClassOut":
        job = (
            None
            if row.open_job_id is None
            else OpenJobOut(id=row.open_job_id, kind=row.open_job_kind, state=row.open_job_state)
        )
        has_saved = row.saved_count > 0
        return cls(
            id=row.id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            current_revision_id=row.current_revision_id,
            current_revision_number=row.current_revision_number,
            revision_count=row.revision_count,
            saved=SavedOut(
                count=row.saved_count,
                latest_revision_number=row.saved_revision_number,
                latest_saved_at=row.saved_at,
                latest_up_to_date=bool(row.saved_up_to_date) if has_saved else None,
                latest_saved_workout_id=row.saved_workout_id if has_saved else None,
                saved_state=(
                    "none" if not has_saved else "ok" if row.saved_workout_id else "inconsistent"
                ),
                detached_count=int(row.detached_count),
            ),
            open_job_id=row.open_job_id,
            open_job=job,
            profile=[(int(z), int(sec)) for z, sec in row.profile],
            tss_approximate=bool(row.tss_approximate),
        )


class SongsOut(BaseModel):
    """The class's newest song list (ADR 0008 section 9). ``content_hash`` is what Approve sends
    back with ``revision_id``; ``state`` is ``none``, ``waiting``, ``approved``, ``expired`` or
    ``cancelled``."""

    state: Literal["none", "waiting", "approved", "expired", "superseded", "cancelled"]
    job_id: UUID | None = None
    revision_id: UUID | None = None
    content_hash: str | None = None
    expires_at: datetime | None = None


class PlaylistOut(BaseModel):
    """A playlist classbeat made in the owner's own Spotify; ``link`` is theirs alone (built from
    the stored id, only once the playlist is verified)."""

    name: str
    link: str | None = None
    detached: bool = False
    note: str | None = None  # a detached one: it is in the earlier Spotify account


class PlaylistNameOut(BaseModel):
    """A playlist left in Spotify as the class-delete answer names it: no link (ADR 0008 section
    2: links are shown on the class page only)."""

    name: str
    detached: bool = False


class ClassDetailOut(ClassOut):
    songs: SongsOut
    playlist: PlaylistOut | None = None  # the current draft's own playlist
    spotify_playlists: list[PlaylistOut]  # every one left in Spotify if the class is deleted
    # The brief the class was designed (or its songs picked) for, to seed a follow-up job such as
    # Ask for changes or a resume (ADR 0008 section 6); the owner's own, never another user's.
    brief: Brief | None = None


class MosaicSongOut(BaseModel):
    """One of the up to four songs of a playlist's mosaic: its title (for the initials tile) and
    its album cover on the pinned host, or null. No Spotify id."""

    title: str
    cover: str | None = None


class MusicPlaylistOut(BaseModel):
    """A playlist classbeat made in the owner's own Spotify, for the Music page. ``link`` is the
    owner's own, built by the API from the stored id of a verified playlist (ADR 0013 section 4)."""

    class_id: UUID
    class_title: str
    name: str
    link: str | None = None
    detached: bool = False
    note: str | None = None
    songs: list[MosaicSongOut]


class MusicPlaylistsOut(BaseModel):
    playlists: list[MusicPlaylistOut]


class ClassesOut(BaseModel):
    """One page of the user's classes, newest first. ``next_cursor`` is the opaque ``cursor`` of
    the next page, ``None`` on the last."""

    classes: list[ClassOut]
    next_cursor: str | None = None


class SummaryOut(BaseModel):
    segments: int
    intervals: int
    total_sec: int


class RevisionSummaryOut(BaseModel):
    id: UUID
    number: int
    parent_id: UUID | None
    parent_number: int | None
    origin: str
    created_at: datetime
    is_current: bool
    summary: SummaryOut
    cost_micros: int

    @classmethod
    def of(cls, row: Any) -> "RevisionSummaryOut":
        return cls(
            id=row.id,
            number=row.number,
            parent_id=row.parent_id,
            parent_number=row.parent_number,
            origin=row.origin,
            created_at=row.created_at,
            is_current=bool(row.is_current),
            summary=SummaryOut(
                segments=row.segments or 0,
                intervals=row.intervals or 0,
                total_sec=int(row.total_sec or 0),
            ),
            cost_micros=int(row.cost_micros),
        )


class RevisionsOut(BaseModel):
    revisions: list[RevisionSummaryOut]


class UnknownBpmOut(BaseModel):
    index: int
    title: str


class UsageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class ProvenanceOut(BaseModel):
    """What a revision records about how it was made, minus the run ids. Unknown keys are
    dropped, never passed through."""

    model_config = ConfigDict(extra="ignore")
    origin: str | None = None
    base_revision_id: UUID | None = None
    request_message_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    model_answered: str | None = None
    effort: str | None = None
    prompt_version: str | None = None
    classbeat_version: str | None = None
    mowl_version: str | None = None
    map_version: int | None = None
    crossfade_sec: float | None = None
    usage: UsageOut | None = None
    cost_micros: int | None = None


class RevisionOut(RevisionSummaryOut):
    kind: str
    content: dict[str, Any]
    unknown_bpm: list[UnknownBpmOut]
    provenance: ProvenanceOut
    # ADR 0012 section 7: the draft has a zone 7 interval, so its TSS is approximate.
    tss_approximate: bool = False


class MessageOut(BaseModel):
    seq: int
    role: str
    body: str
    job_id: UUID | None
    base_revision_id: UUID | None
    result_revision_id: UUID | None
    change: dict[str, Any] | None
    created_at: datetime


class MessagesOut(BaseModel):
    messages: list[MessageOut]


class PickIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: UUID
    content_hash: str


class DeletedOut(BaseModel):
    deleted: bool
    mowl_workouts_kept: int
    note: str
    spotify_playlists_left: list[PlaylistNameOut] = []
    spotify_note: str | None = None


class SongOut(BaseModel):
    """A song of a list as the page shows it: no bare Spotify id. ``cover`` is the album cover URL
    on the pinned host (ADR 0013 section 9, checked again just before it is returned) or null.
    ``open_spotify`` is the server-built link back to the song (ADR 0008 section 9, Amendment 6)
    or null."""

    position: int
    title: str
    artist: str
    role: str
    bpm: float
    length_sec: int
    new: bool
    cover: str | None = None
    open_spotify: str | None = None


class SongListOut(BaseModel):
    id: UUID
    number: int
    kind: Literal["song_list"] = "song_list"
    created_at: datetime
    parent_id: UUID | None
    parent_number: int | None
    status: Literal["waiting", "approved", "expired", "superseded", "cancelled"]
    job_id: UUID | None
    expires_at: datetime | None
    approved_at: datetime | None
    content_hash: str
    mode: str
    target_sec: int
    total_sec: int
    crossfade_sec: float
    songs: list[SongOut]
    omitted_no_bpm: int
    note: str


class DismissedRulesOut(BaseModel):
    """The rule ids dismissed for a class (ADR 0017 section 4), in the order they were dismissed."""

    dismissed_rules: list[str]

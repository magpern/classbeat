"""Response models of the class API. Nothing here names a MOWL or Spotify id, a link id, a
fingerprint, an agent run id, a hash or lease data (ADR 0007 section 12)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OpenJobOut(BaseModel):
    id: UUID
    kind: str
    state: str


class SavedOut(BaseModel):
    """Summary of the live (not detached) MOWL workouts classbeat made for the class.

    ``latest_saved_workout_id`` is classbeat's own id of the newest live ``saved_workouts`` row,
    the one Update names (ADR 0007 section 5), never a MOWL program id; ``None`` when the class
    has no live row or that row is not one Update would accept (its creating operation is of
    another class). ``detached_count`` counts the rows detached by a MOWL disconnect, reconnect
    or ``keys wipe`` (section 9)."""

    count: int
    latest_revision_number: int | None
    latest_saved_at: datetime | None
    latest_up_to_date: bool | None
    latest_saved_workout_id: UUID | None
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
                detached_count=int(row.detached_count),
            ),
            open_job_id=row.open_job_id,
            open_job=job,
        )


class ClassesOut(BaseModel):
    classes: list[ClassOut]


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

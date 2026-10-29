"""Job event payloads (ADR 0005 section 6): one Pydantic model per event kind, and the wake-up.

Every event a writer inserts goes through ``validated_payload``, and every event the SSE stream
sends goes through it again, so a row that holds anything else (a field the model does not name,
a long text, a nested value) is never shown. Each model has ``extra="forbid"`` and holds only enum
values, ids of the owner's own rows, counts and timestamps. Never in a payload: tokens or
credentials, error or exception texts, model text, tool arguments or results, track titles,
artists or other provider data, playlist ids or URLs. ``tool_called.error`` is an enum code.

``notify_event`` runs ``pg_notify('job_events', <job id>)`` in the transaction that inserts an
event: only the id, delivered at commit (a rolled-back event wakes nobody). The web processes
listen (``classbeat.jobs.hub``) and read in a user session, so a notification for someone else's
job reveals nothing. ``docker/postgres/queue/20-queue.sql`` does the same for the events the
queue functions insert (a trigger would break the queue check, ADR 0005 section 3).
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs.errors import CATEGORIES

CHANNEL: Final = "job_events"
NOTIFY = text("SELECT pg_notify('job_events', :job_id)")

# A stage or tool name: lower-case words, as the kind registry and the dispatcher spell them.
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class ToolError(StrEnum):
    """Why a tool call failed, as a code. Never the error text (it can hold provider data)."""

    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    NOT_ALLOWED = "not_allowed"
    NOT_FOUND = "not_found"
    LIMIT_EXCEEDED = "limit_exceeded"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    INTERNAL = "internal"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Empty(Payload):
    """``queued``, ``approved``, ``cancel_requested``, ``cancelled``, ``expired``, ``requeued``."""


class Claimed(Payload):
    attempt: Annotated[int, Field(ge=1, le=10)]


class StageStarted(Payload):
    stage: Name


class ToolCalled(Payload):
    tool: Name
    ok: bool
    error: ToolError | None = None


class RevisionCreated(Payload):
    revision_id: UUID
    kind: Literal["draft", "song_list"]
    number: Annotated[int, Field(ge=1)]


class WaitingForApproval(Payload):
    revision_id: UUID
    expires_at: datetime


class _Category(Payload):
    category: str

    @field_validator("category")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError("unknown failure category")
        return value


class RetryScheduled(_Category):
    available_at: datetime


class Completed(Payload):
    revision_id: UUID | None = None


class Failed(_Category):
    pass


class ExternalOperation(Payload):
    kind: Name
    state: Literal["pending", "succeeded", "failed", "uncertain"]


# ADR 0005 section 6; the same kinds as the ``job_events.kind`` check of migration 0004.
MODELS: Final[Mapping[str, type[Payload]]] = {
    "queued": Empty,
    "claimed": Claimed,
    "stage_started": StageStarted,
    "tool_called": ToolCalled,
    "revision_created": RevisionCreated,
    "waiting_for_approval": WaitingForApproval,
    "approved": Empty,
    "retry_scheduled": RetryScheduled,
    "cancel_requested": Empty,
    "cancelled": Empty,
    "completed": Completed,
    "failed": Failed,
    "expired": Empty,
    "requeued": Empty,
    "external_operation": ExternalOperation,
}
# The stream ends after one of these (the job is over) and after ``waiting_for_approval``.
TERMINAL_KINDS: Final = frozenset({"completed", "failed", "cancelled", "expired"})


def validated_payload(kind: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The payload as it is stored and shown: validated against the kind's model, JSON-ready
    (ids and timestamps as text), ``None`` fields left out. ``ValueError`` (without the payload,
    which may be what is wrong with it) for an unknown kind or a payload the model refuses."""
    model = MODELS.get(kind)
    if model is None:
        raise ValueError("unknown event kind")
    try:
        return model.model_validate(dict(payload or {})).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ValueError(f"the payload of a {kind!r} event does not match its model") from None


async def notify_event(db: AsyncSession, job_id: UUID) -> None:
    """Wake the web processes' streams for ``job_id`` when the transaction commits."""
    await db.execute(NOTIFY, {"job_id": str(job_id)})
